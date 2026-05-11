import os
import subprocess
import sys

# Re-exec under the project venv if the user launched us with a different
# interpreter (system python lacks httpx/dotenv/etc and would crash on the
# imports below). Must run before any third-party import. We use subprocess
# rather than os.execv because the latter on Windows builds a flat command
# line and a space in the path (e.g. "AI model") tears the argv apart.
_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
_venv_python = os.path.join(_PROJECT_ROOT, ".venv", "Scripts", "python.exe")
if not os.path.isfile(_venv_python):
    _venv_python = os.path.join(_PROJECT_ROOT, ".venv", "bin", "python")
if os.path.isfile(_venv_python) and os.path.realpath(sys.executable) != os.path.realpath(_venv_python):
    print(f"⚙️  Перезапуск через venv: {_venv_python}")
    sys.exit(subprocess.run(
        [_venv_python, os.path.abspath(__file__), *sys.argv[1:]],
        cwd=_PROJECT_ROOT,
    ).returncode)

import re
import time
import httpx
import threading
import logging
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from backend.logging_setup import setup_logging

_logs_path = Path(os.getenv("LOGS_PATH", "./logs/"))
if not _logs_path.is_absolute():
    _logs_path = Path(_PROJECT_ROOT) / _logs_path
setup_logging(_logs_path)
log = logging.getLogger("start")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
PROJECT_ROOT = _PROJECT_ROOT

# Backend (and the training subprocess it spawns) must run on the venv
# interpreter, otherwise torch/transformers/bitsandbytes will be missing.
_venv_python_win = os.path.join(PROJECT_ROOT, ".venv", "Scripts", "python.exe")
_venv_python_nix = os.path.join(PROJECT_ROOT, ".venv", "bin", "python")
if os.path.isfile(_venv_python_win):
    BACKEND_PYTHON = _venv_python_win
elif os.path.isfile(_venv_python_nix):
    BACKEND_PYTHON = _venv_python_nix
else:
    log.error(
        "❌ Не найден интерпретатор venv в %s. "
        "Создай venv (py -3.11 -m venv .venv), активируй его, "
        "поставь зависимости и CUDA-сборку torch.",
        os.path.join(PROJECT_ROOT, ".venv"),
    )
    sys.exit(1)

tunnel_url = None

def stream_output(process, prefix=""):
    """Читает вывод процесса в фоне и пишет в лог с префиксом"""
    for line in process.stdout:
        line = line.strip()
        if line:
            log.info("%s %s", prefix, line)

def start_backend():
    log.info("🔧 Запускаю бэкенд (%s)...", BACKEND_PYTHON)
    process = subprocess.Popen(
        [BACKEND_PYTHON, "-u", "-m", "backend.main"],
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding='utf-8',
        errors='ignore'
    )
    threading.Thread(target=stream_output, args=(process, "[backend]"), daemon=True).start()
    return process

def start_frontend():
    log.info("🎨 Запускаю фронтенд...")
    process = subprocess.Popen(
        ["npm", "run", "dev"],
        cwd=os.path.join(PROJECT_ROOT, "frontend"),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        shell=True,
        encoding='utf-8',
        errors='ignore'
    )
    threading.Thread(target=stream_output, args=(process, "[frontend]"), daemon=True).start()
    return process

_TUNNEL_URL_RE = re.compile(r'https://[a-z0-9\-]+\.trycloudflare\.com')


def _spawn_cloudflared_once(timeout: float = 25.0):
    """Spawn cloudflared and wait up to `timeout` seconds for the trycloudflare URL.

    Returns (process, url). url is None if the tunnel didn't come up — the caller
    is responsible for terminating the process before retrying.
    """
    process = subprocess.Popen(
        [".\\cloudflared", "tunnel", "--url", "http://localhost:8000"],
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding='utf-8',
        errors='ignore'
    )

    deadline = time.time() + timeout
    while True:
        if process.poll() is not None:
            # cloudflared exited; drain anything left
            for line in process.stdout:
                line = line.strip()
                if line:
                    log.info("[cloudflare] %s", line)
            return process, None

        line = process.stdout.readline()
        if not line:
            if time.time() > deadline:
                return process, None
            continue

        line = line.strip()
        if line:
            log.info("[cloudflare] %s", line)
        match = _TUNNEL_URL_RE.search(line)
        if match:
            url = match.group(0)
            log.info("✅ Tunnel URL: %s", url)
            # После получения URL читаем остаток в фоне
            threading.Thread(target=stream_output, args=(process, "[cloudflare]"), daemon=True).start()
            return process, url

        if time.time() > deadline:
            return process, None


def _kill(process):
    if process is None:
        return
    try:
        process.terminate()
        process.wait(timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
        except OSError:
            pass


def start_cloudflare():
    """Try to bring up a quick tunnel with retries. Returns the live process or None."""
    global tunnel_url
    backoffs = [5, 10, 20, 40]  # 5 attempts total: immediate, then 4 backoff waits
    for attempt in range(1, len(backoffs) + 2):
        log.info("🚀 Запускаю Cloudflare tunnel (попытка %d/%d)...", attempt, len(backoffs) + 1)
        process, url = _spawn_cloudflared_once()
        if url:
            tunnel_url = url
            return process

        _kill(process)
        if attempt > len(backoffs):
            break
        delay = backoffs[attempt - 1]
        log.warning(
            "⏳ Cloudflare quick tunnel не поднялся "
            "(скорее всего временный 500 на trycloudflare.com). "
            "Повтор через %dс...",
            delay,
        )
        time.sleep(delay)

    log.warning(
        "⚠️  Cloudflare tunnel так и не запустился после ретраев. "
        "Backend (http://localhost:8000) и dashboard (http://localhost:5173) работают, "
        "но Telegram webhook зарегистрировать не удалось — бот не получит входящие "
        "сообщения, пока quick-tunnels у Cloudflare лежат. Можно перезапустить позже "
        "или поднять named-tunnel (требует аккаунта CF)."
    )
    return None

def register_webhook(url):
    log.info("⏳ Жду 10с перед регистрацией webhook (%s)...", url)
    time.sleep(10)

    log.info("📡 Регистрирую webhook в Telegram...")
    backoffs = [3, 6, 12, 24]
    last_error = None
    for attempt in range(1, len(backoffs) + 2):
        try:
            response = httpx.get(
                f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook",
                params={"url": f"{url}/webhook/{BOT_TOKEN}"},
                timeout=10
            )
            data = response.json()
            if data.get("ok"):
                log.info("✅ Webhook зарегистрирован!")
                return
            last_error = data
            desc = (data.get("description") or "").lower()
            # Только DNS-резолв имеет смысл ретраить — остальные ошибки конфигурации
            # сами не починятся, и повторы лишь зашумят логи.
            transient = "failed to resolve host" in desc or "name or service not known" in desc
            if not transient:
                log.error("❌ Ошибка webhook: %s", data)
                return
        except Exception as e:
            last_error = e

        if attempt > len(backoffs):
            break
        delay = backoffs[attempt - 1]
        log.info("⏳ Telegram ещё не резолвит туннель, повтор через %dс (попытка %d/%d)...", delay, attempt, len(backoffs) + 1)
        time.sleep(delay)

    log.error("❌ Ошибка webhook после ретраев: %s", last_error)

if __name__ == "__main__":
    # Запускаем бэкенд и фронтенд параллельно
    backend = start_backend()
    frontend = start_frontend()

    log.info("⏳ Жду запуска сервисов...")
    time.sleep(4)

    # Запускаем tunnel с ретраями (None если все попытки провалились)
    cloudflare = start_cloudflare()

    if tunnel_url:
        register_webhook(tunnel_url)
        log.info("=" * 50)
        log.info("✅ ВСЁ ЗАПУЩЕНО!")
        log.info("🌐 Публичный URL: %s", tunnel_url)
        log.info("📊 Дашборд:       http://localhost:5173")
        log.info("📡 API docs:      http://localhost:8000/docs")
        log.info("=" * 50)
        log.info("Нажми Ctrl+C для остановки")
    else:
        log.warning("=" * 50)
        log.warning("⚠️  ЗАПУЩЕНО ЧАСТИЧНО (без внешнего туннеля)")
        log.warning("📊 Дашборд:       http://localhost:5173")
        log.warning("📡 API docs:      http://localhost:8000/docs")
        log.warning("   Telegram webhook не зарегистрирован.")
        log.warning("=" * 50)
        log.warning("Нажми Ctrl+C для остановки")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("🛑 Остановка всех процессов...")
        _kill(backend)
        _kill(frontend)
        _kill(cloudflare)
        log.info("👋 Готово")