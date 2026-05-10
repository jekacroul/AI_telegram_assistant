import subprocess
import re
import time
import httpx
import os
import threading
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
PROJECT_ROOT = "D:\\AI model\\AI_telegram_assistant"

tunnel_url = None

def stream_output(process, prefix=""):
    """Читает вывод процесса в фоне и печатает с префиксом"""
    for line in process.stdout:
        line = line.strip()
        if line:
            print(f"{prefix} {line}")

def start_backend():
    print("🔧 Запускаю бэкенд...")
    process = subprocess.Popen(
        ["python", "-m", "backend.main"],
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
    print("🎨 Запускаю фронтенд...")
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

def start_cloudflare():
    global tunnel_url
    print("🚀 Запускаю Cloudflare tunnel...")
    process = subprocess.Popen(
        [".\\cloudflared", "tunnel", "--url", "http://localhost:8000"],
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding='utf-8',
        errors='ignore'
    )

    for line in process.stdout:
        line = line.strip()
        if line:
            print(f"[cloudflare] {line}")
        match = re.search(r'https://[a-z0-9\-]+\.trycloudflare\.com', line)
        if match:
            tunnel_url = match.group(0)
            print(f"\n✅ Tunnel URL: {tunnel_url}")
            # После получения URL читаем остаток в фоне
            threading.Thread(target=stream_output, args=(process, "[cloudflare]"), daemon=True).start()
            break

    return process

def register_webhook(url):
    print(f"📡 Регистрирую webhook в Telegram...")
    try:
        response = httpx.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook",
            params={"url": f"{url}/webhook/{BOT_TOKEN}"},
            timeout=10
        )
        data = response.json()
        if data.get("ok"):
            print(f"✅ Webhook зарегистрирован!")
        else:
            print(f"❌ Ошибка webhook: {data}")
    except Exception as e:
        print(f"❌ Ошибка: {e}")

if __name__ == "__main__":
    # Запускаем бэкенд и фронтенд параллельно
    backend = start_backend()
    frontend = start_frontend()

    print("⏳ Жду запуска сервисов...")
    time.sleep(4)

    # Запускаем tunnel (блокирует пока не получит URL)
    cloudflare = start_cloudflare()

    # Регистрируем webhook
    time.sleep(8)
    if tunnel_url:
        register_webhook(tunnel_url)
        print("\n" + "="*50)
        print("✅ ВСЁ ЗАПУЩЕНО!")
        print(f"🌐 Публичный URL: {tunnel_url}")
        print(f"📊 Дашборд:       http://localhost:5173")
        print(f"📡 API docs:       http://localhost:8000/docs")
        print("="*50)
        print("\nНажми Ctrl+C для остановки\n")
    else:
        print("❌ Tunnel не запустился")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n🛑 Остановка всех процессов...")
        backend.terminate()
        frontend.terminate()
        cloudflare.terminate()
        print("👋 Готово")