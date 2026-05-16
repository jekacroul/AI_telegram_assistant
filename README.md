# Telegram Local AI Assistant

Полностью локальный Telegram-ассистент, который учится твоему стилю общения через
LoRA fine-tuning локального LLM (Mistral 7B / LLaMA 3.1 8B / Saiga и т.п.) и
отвечает собеседникам от твоего имени. Никаких внешних API — все данные, модели
и обучение остаются на твоей машине.

## Возможности

- **Сбор переписки**. Каждое входящее/исходящее сообщение сохраняется в SQLite.
- **Анализ стиля**. Автоматически собирается профиль стиля: длина реплик,
  эмодзи, тон, частые слова, приветствия.
- **Генерация ответов**. Локальный LLM выдаёт варианты ответа с учётом профиля
  стиля и истории чата.
- **Векторная память (RAG)**. Семантический поиск по всей истории переписки
  через ChromaDB — бот «помнит» больше, чем последние сообщения.
- **LoRA fine-tuning**. Обучение адаптера на собранных парах прямо из веб-панели,
  с авто-экспортом в GGUF.
- **Голосовые сообщения**. Локальная транскрипция через openai-whisper.
- **Авто-запуск llama-server**. Бэкенд сам поднимает llama.cpp HTTP-сервер с
  базовой моделью и активным LoRA — LM Studio не обязательна.
- **Telegram admin-панель**. Управление ботом командами из своего личного чата.
- **Режим репликации**. Импорт личного экспорта Telegram для обучения.
- **Веб-дашборд**. React-панель: диалоги, очередь ответов, обучение, статистика,
  настройки.

## Стек

- **LLM**: локальный OpenAI-совместимый сервер ([LM Studio](https://lmstudio.ai),
  llama.cpp server, vLLM) — либо автозапуск llama-server бэкендом.
- **Fine-tuning**: PEFT + transformers + bitsandbytes (4-bit NF4) + TRL/SFTTrainer
- **Telegram**: aiogram 3.x (Bot API, async), доставка через webhook
- **Backend**: FastAPI + SQLAlchemy (async) + SQLite + SSE
- **Frontend**: React + Vite + Tailwind
- **RAG**: ChromaDB + sentence-transformers
- **Голос**: openai-whisper + ffmpeg
- **GPU**: NVIDIA 8 GB+ (для обучения), CUDA-сборка PyTorch

## Структура проекта

```
AI_telegram_assistant/
├── start.py                  # Лаунчер: backend + frontend + cloudflare tunnel
├── backend/
│   ├── main.py               # FastAPI + lifespan, все API-роуты
│   ├── bot.py                # aiogram bot, обработчик webhook
│   ├── admin_bot.py          # Telegram admin-панель для владельца
│   ├── llm_engine.py         # OpenAI-совместимый LLM-клиент
│   ├── llama_server.py       # Авто-запуск llama.cpp server
│   ├── trainer.py            # LoRA fine-tuning pipeline
│   ├── train_worker.py       # Подпроцесс обучения
│   ├── dataset_builder.py    # Сбор обучающих пар
│   ├── gguf_export.py        # Конвертация/квантизация в GGUF
│   ├── merge_worker.py       # Слияние LoRA с базовой моделью
│   ├── rag_engine.py         # Векторная память (ChromaDB)
│   ├── style_engine.py       # Анализ стиля
│   ├── whisper_engine.py     # Транскрипция голосовых
│   ├── replication.py        # Импорт экспорта Telegram
│   ├── quality_filter.py     # Фильтр качества обучающих пар
│   ├── schedule.py           # Расписание активности бота
│   ├── delay.py              # Имитация задержки ответа
│   ├── dialog_backup.py      # Бэкап диалогов
│   ├── database.py           # SQLAlchemy модели
│   └── config.py             # Настройки из .env
├── frontend/
│   └── src/
│       ├── pages/            # Dashboard, Dialogs, QuickReplies, Replication,
│       │                     # Settings, Stats, StyleProfile, Training
│       └── components/       # MessageCard, ReplyModal, StatusDot
├── data/                     # SQLite БД + ChromaDB (создаётся автоматически)
├── training_data/            # JSONL датасеты
├── models/                   # LoRA адаптеры, кэш HF/sentence-transformers
├── logs/                     # Логи
├── media/                    # Скачанные медиа/голосовые
├── .env.example
├── requirements.txt
└── README.md
```

> Папки `data/`, `logs/`, `media/`, `models/`, `training_data/` и `.env`
> в `.gitignore` — они создаются при первом запуске.

---

# Установка с нуля

Эта инструкция рассчитана на **полностью пустую машину** — предполагается, что
ничего, кроме операционной системы, не установлено. Делай шаги по порядку.

## 0. Что в итоге будет установлено

| Компонент | Зачем | Версия |
| --- | --- | --- |
| Python | Backend, обучение, бот | **3.11** |
| Git | Скачать проект | любая свежая |
| Node.js + npm | Веб-дашборд (frontend) | 18+ |
| ffmpeg | Транскрипция голосовых | любая свежая |
| cloudflared | Публичный туннель для Telegram webhook | любая свежая |
| NVIDIA-драйвер | GPU для обучения и инференса | актуальный |
| Telegram bot token | Сам бот | — |

> **GPU.** Для LoRA-обучения нужна видеокарта NVIDIA 8 GB+ с актуальным
> драйвером. Без GPU можно собирать данные и пользоваться панелью, но обучение
> и быстрый инференс работать не будут.

---

## 1. Python 3.11

### Windows

1. Скачай установщик с <https://www.python.org/downloads/release/python-3119/>
   (раздел *Windows installer (64-bit)*).
2. Запусти его и **обязательно поставь галочку «Add python.exe to PATH»**.
3. Нажми *Install Now*.
4. Проверь в новом окне терминала (PowerShell или cmd):

   ```bash
   py -3.11 --version
   ```

### Linux (Ubuntu/Debian)

```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv python3.11-dev
python3.11 --version
```

### macOS

```bash
brew install python@3.11
python3.11 --version
```

## 2. Git

- **Windows**: скачай и установи с <https://git-scm.com/download/win> (все
  настройки по умолчанию подходят).
- **Linux**: `sudo apt install -y git`
- **macOS**: `brew install git` (или установится вместе с Xcode CLT).

Проверка: `git --version`.

## 3. Node.js + npm (для веб-дашборда)

- **Windows / macOS**: скачай LTS-версию с <https://nodejs.org/> и установи.
- **Linux**:

  ```bash
  curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
  sudo apt install -y nodejs
  ```

Проверка: `node --version` и `npm --version`.

## 4. ffmpeg (для голосовых сообщений)

Транскрипция голосовых через Whisper требует ffmpeg в PATH:

```bash
# Windows (через встроенный winget)
winget install ffmpeg
# перезапусти терминал после установки

# Linux
sudo apt install -y ffmpeg

# macOS
brew install ffmpeg
```

Проверка: `ffmpeg -version`.

> Если на Windows `winget` недоступен — скачай сборку с
> <https://www.gyan.dev/ffmpeg/builds/> (gyan.dev → *release essentials*),
> распакуй и добавь папку `bin` в системную переменную PATH.

## 5. cloudflared (публичный туннель)

Telegram доставляет сообщения боту через webhook — нужен публичный HTTPS-URL.
`start.py` поднимает бесплатный Cloudflare quick-tunnel автоматически, но для
этого в **корне проекта** должен лежать бинарник `cloudflared`.

- **Windows**: скачай `cloudflared-windows-amd64.exe` со страницы релизов
  <https://github.com/cloudflare/cloudflared/releases/latest>, переименуй
  в `cloudflared.exe` и положи в корень проекта.
- **Linux**:

  ```bash
  wget https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -O cloudflared
  chmod +x cloudflared
  ```

- **macOS**: `brew install cloudflared` (или скачай `cloudflared-darwin-amd64.tgz`
  с той же страницы релизов и распакуй бинарник в корень проекта).

> Альтернатива cloudflared — [ngrok](https://ngrok.com) или собственный домен;
> тогда webhook регистрируется вручную (см. шаг 13).

## 6. NVIDIA-драйвер

Скачай и установи актуальный драйвер для своей видеокарты с
<https://www.nvidia.com/Download/index.aspx>. После установки проверь:

```bash
nvidia-smi
```

Команда покажет модель GPU и поддерживаемую версию CUDA — она понадобится на
шаге 9 при установке PyTorch. Отдельно ставить CUDA Toolkit не нужно: CUDA-сборка
PyTorch уже содержит нужные библиотеки.

---

## 7. Клонирование проекта и виртуальное окружение

```bash
git clone https://github.com/jekacroul/ai_telegram_assistant.git
cd ai_telegram_assistant

# Создание venv (Windows)
py -3.11 -m venv .venv
.venv\Scripts\activate

# Создание venv (Linux/macOS)
python3.11 -m venv .venv
source .venv/bin/activate
```

> `start.py` сам перезапускается под `.venv`, поэтому важно, чтобы окружение
> лежало именно в `.venv/` в корне проекта.

## 8. Установка зависимостей Python

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

## 9. CUDA-сборка PyTorch (обязательно для обучения)

Дефолтный `torch` из `requirements.txt` на Windows ставится в **CPU-only**
режиме, и LoRA-обучение упадёт с `CUDA is not available`. Переустанови torch
под версию CUDA, которую показал `nvidia-smi` (шаг 6):

```bash
pip uninstall -y torch torchvision torchaudio
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
```

(или `cu124` / `cu118` — под нужную версию CUDA). Проверка:

```bash
python -c "import torch; print(torch.cuda.is_available(), torch.version.cuda)"
```

Должно вывести `True` и версию CUDA.

## 10. Настройка .env

Скопируй пример и отредактируй:

```bash
# Windows
copy .env.example .env
# Linux/macOS
cp .env.example .env
```

Минимально нужно заполнить:

| Переменная | Описание |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | Токен бота от @BotFather |
| `USER_NAME` | Твоё имя — как бот тебя называет в промптах |
| `OPENAI_BASE_URL` | URL локального LLM-сервера (по умолчанию `http://localhost:1234/v1`) |
| `OPENAI_MODEL` | Имя модели на сервере |

Остальные параметры — см. раздел [Конфигурация .env](#конфигурация-env) ниже.

## 11. LLM-сервер — два варианта

### ✅ Вариант B (предпочтительный). Свой llama-server — чтобы цеплялись LoRA-адаптеры

**Рекомендуется именно этот вариант.** Бэкенд сам поднимает llama.cpp
HTTP-сервер с базовой `.gguf`-моделью и **автоматически подцепляет активный
LoRA-адаптер**, обученный на твоём стиле. Так бот реально использует результаты
fine-tuning — при варианте A (LM Studio) адаптеры не подключаются.

1. Скачай базовую модель в формате `.gguf` (например Mistral 7B Instruct,
   Saiga, LLaMA 3.1 8B — квантизация Q4_K_M / Q5_K_M / Q8_0) с
   [Hugging Face](https://huggingface.co/models?library=gguf).
2. Получи бинарник `llama-server`: скачай готовую сборку со страницы релизов
   <https://github.com/ggerganov/llama.cpp/releases> (например
   `llama-bXXXX-bin-win-cuda-x64.zip` под Windows + CUDA) или собери llama.cpp
   самостоятельно.
3. Пропиши в `.env`:

   ```ini
   OPENAI_BASE_URL=http://127.0.0.1:1234/v1
   LLAMA_SERVER_AUTO_START=true
   LLAMA_BASE_MODEL_GGUF=C:\models\mistral-7b-instruct-Q5_K_M.gguf
   LLAMA_SERVER_BIN=C:\llama.cpp\llama-server.exe   # путь к бинарнику
   LLAMA_SERVER_PORT=1234
   LLAMA_SERVER_NGL=99      # слоёв на GPU (99 = все)
   LLAMA_SERVER_CTX=4096
   ```

`OPENAI_BASE_URL` должен указывать на тот же порт, что `LLAMA_SERVER_PORT`.
После обучения адаптер подключается автоматически при следующем запуске сервера
(или через `/api/training/activate/{id}`).

### Вариант A. LM Studio (проще, но без адаптеров)

Годится для первого знакомства и сбора данных, **но обученные LoRA-адаптеры
подключаться не будут** — бот всегда отвечает базовой моделью.

1. Скачай [LM Studio](https://lmstudio.ai).
2. Загрузи нужную модель (Mistral 7B Instruct, Saiga, LLaMA 3.1 8B и т.п.).
3. Запусти встроенный OpenAI-совместимый сервер (вкладка **Developer / Local Server**).
4. В `.env` оставь `OPENAI_BASE_URL=http://localhost:1234/v1` и
   `LLAMA_SERVER_AUTO_START=false`.

Подойдёт любой OpenAI-совместимый бэкенд (vLLM и т.п.) — укажи его URL.

## 12. Frontend

```bash
cd frontend
npm install
cd ..
```

## 13. Webhook / публичный доступ

Telegram доставляет сообщения боту через webhook, а значит нужен публичный
HTTPS-URL, ведущий на бэкенд (`:8000`).

- **Автоматически**: `start.py` запускает [cloudflared](https://github.com/cloudflare/cloudflared)
  quick-tunnel и сам регистрирует webhook. Убедись, что бинарник `cloudflared`
  лежит в корне проекта (шаг 5).
- **Вручную**: подними любой туннель ([ngrok](https://ngrok.com), cloudflared,
  свой домен) и зарегистрируй webhook:

  ```
  POST /api/webhook/set { "url": "https://your-public-host/webhook/<TOKEN>" }
  ```

---

# Запуск

## Способ 1. Всё одной командой (рекомендуется)

```bash
python start.py
```

`start.py`:

1. Перезапускается под `.venv`.
2. Поднимает backend (`backend.main` на `:8000`).
3. Поднимает frontend (`npm run dev` на `:5173`).
4. Запускает Cloudflare quick-tunnel (с ретраями) и регистрирует webhook.

После старта:

- 📊 Дашборд: <http://localhost:5173>
- 📡 API docs: <http://localhost:8000/docs>
- 🌐 Публичный URL — в логах

`Ctrl+C` останавливает все процессы.

## Способ 2. Компоненты по отдельности

```bash
# Терминал 1 — backend
.venv\Scripts\activate
python -m backend.main          # или: uvicorn backend.main:app --host 0.0.0.0 --port 8000

# Терминал 2 — frontend (разработка)
cd frontend
npm run dev                     # http://localhost:5173, проксирует /api на :8000
```

Для прода собери фронтенд — бэкенд сам отдаст `dist/`:

```bash
cd frontend
npm run build
```

---

# Как это работает

1. **Сбор данных**. Каждое входящее и исходящее сообщение сохраняется в SQLite.
2. **Анализ стиля** (`style_engine`). Каждые ~20 новых сообщений профиль стиля
   пересобирается: средняя длина, эмодзи, тон, частые слова, приветствия.
3. **Генерация ответа** (`llm_engine`). На входящее сообщение локальный LLM
   выдаёт варианты ответа с системным промптом, куда инжектится профиль стиля,
   история чата и найденный RAG-контекст.
4. **Режимы**:
   - `AUTO_REPLY=true` — бот отвечает сам, ты ставишь 👍/👎 и правишь.
   - `AUTO_REPLY=false` — варианты приходят на дашборд, ты выбираешь.
5. **Fine-tuning** (`trainer`). Когда накопилось достаточно пар (input → output),
   запускаешь LoRA-обучение: r=16, alpha=32, target `q_proj,v_proj`, 3 эпохи,
   4-bit NF4, fp16 compute. Адаптер сохраняется в `models/lora_adapter_v{N}/`.
   Если задан `LLAMA_CPP_PATH`, адаптер автоматически сливается с базовой
   моделью и квантизуется в один GGUF (`GGUF_QUANT`, по умолчанию `Q8_0`).
   `/api/training/activate/{run_id}` помечает адаптер активным в БД.
6. **Обратная связь**. 👎 открывает редактор: правишь, re-send, корректная пара
   уходит в `training_pairs` со `feedback=good/bad`.

---

# Голосовые сообщения

Бот принимает голосовые/аудио и транскрибирует их локально через
[openai-whisper](https://github.com/openai/whisper). По умолчанию модель
`large-v3`, язык `ru`.

VRAM (приблизительно): `tiny`/`base` ~1 GB, `small` ~1.5 GB, `medium` ~3 GB,
`large-v3` ~6 GB.

Если основная LLM крупная, включи в **Settings → Голосовые сообщения** опцию
*«Освобождать VRAM после транскрипции»* (lazy_load): Whisper грузится перед
обработкой и выгружается сразу после.

Режимы реакции на голосовые (Settings):

- *Отвечать текстом автоматически* — транскрипция → генерация → отправка.
- *Добавлять в очередь для ручного ответа* — голосовые попадают в pending.
- *Игнорировать голосовые*.

Низкая уверенность транскрипции (avg confidence < 0.5) автоматически переводит
сообщение в pending.

---

# Векторная память (RAG)

Каждое сообщение векторизуется и складывается в локальную базу
[ChromaDB](https://www.trychroma.com/); при генерации ответа бот находит
семантически близкие сообщения по всей истории. Эмбеддинги —
`paraphrase-multilingual-mpnet-base-v2`.

Установка/проверка:

```bash
pip install chromadb sentence-transformers
```

Модели скачиваются в `models/huggingface` и `models/sentence_transformers`,
векторная база — в `data/chroma_db/` (~40 КБ на 1000 сообщений).

При первом запуске бот автоматически индексирует существующие сообщения
(~1–5 мин, прогресс в логах). Новые сообщения индексируются в фоне.

Тест поиска: **Training → Векторная память → Тест поиска**.
Настройка: **Settings → Векторная память (RAG)** — порог схожести, число
результатов, поиск по всем чатам.

---

# Telegram admin-панель

Бот принимает админ-команды из твоего личного чата. В `.env`:

```
ADMIN_BOT_ENABLED=true
OWNER_CHAT_ID=        # твой личный chat_id
```

`chat_id` определяется автоматически: напиши `/start` боту в личку, затем
нажми «Определить автоматически» в **Settings → Admin Panel**. То же касается
`NOTIFY_CHAT_ID` — чат для уведомлений об авто-ответах.

---

# Конфигурация .env

```ini
# --- Telegram ---
TELEGRAM_BOT_TOKEN=                 # токен от @BotFather
WEBHOOK_BASE_URL=                   # базовый публичный URL (если задаёшь вручную)

# --- LLM (OpenAI-совместимый сервер) ---
OPENAI_BASE_URL=http://localhost:1234/v1
OPENAI_API_KEY=local
OPENAI_MODEL=local-model
LLM_MAX_TOKENS=2048

# --- Поведение ---
AUTO_REPLY=false                    # true = бот отвечает сам
USER_NAME=Я                         # как бот тебя называет

# --- Пути (создаются автоматически) ---
DB_PATH=./data/database.db
TRAINING_DATA_PATH=./training_data/
MODELS_PATH=./models/
LOGS_PATH=./logs/

# --- Fine-tuning ---
HF_BASE_MODEL=mistralai/Mistral-7B-Instruct-v0.2

# --- Авто-экспорт обученной модели в GGUF (необязательно) ---
# LLAMA_CPP_PATH — путь к собранному клону https://github.com/ggerganov/llama.cpp
#   (нужны convert_hf_to_gguf.py и собранный llama-quantize в build/bin/).
# GGUF_QUANT — тип квантизации (Q4_K_M / Q5_K_M / Q8_0 / F16). По умолчанию Q8_0.
LLAMA_CPP_PATH=
GGUF_QUANT=Q8_0

# --- Авто-запуск llama-server (альтернатива LM Studio) ---
LLAMA_BASE_MODEL_GGUF=              # путь к базовой .gguf
LLAMA_SERVER_BIN=                   # путь к llama-server.exe (необязательно)
LLAMA_SERVER_PORT=1234
LLAMA_SERVER_NGL=99                 # слоёв на GPU (99 = все)
LLAMA_SERVER_CTX=4096
LLAMA_SERVER_AUTO_START=true

# --- Уведомления ---
NOTIFY_CHAT_ID=                     # chat_id для уведомлений об авто-ответах

# --- Admin-панель ---
OWNER_CHAT_ID=                      # твой личный chat_id
ADMIN_BOT_ENABLED=true
```

---

# API (выжимка)

| Метод | Путь | Описание |
| --- | --- | --- |
| GET | `/api/status` | Здоровье LLM / Bot / DB |
| GET | `/api/chats` | Список чатов |
| GET/POST | `/api/settings` | Токен, авто-ответ, мониторинг |
| GET | `/api/messages/pending` | Очередь на ручной ответ |
| POST | `/api/reply/generate` | Варианты ответа для message_id |
| POST | `/api/reply/approve` | Одобрить и отправить |
| POST | `/api/reply/feedback` | 👍/👎 + правка |
| GET | `/api/training/status` | Статистика и активный адаптер |
| POST | `/api/training/build-dataset` | Собрать JSONL |
| POST | `/api/training/start` | Запустить обучение |
| GET | `/api/training/progress` | SSE: epoch/step/loss/eta |
| POST | `/api/training/activate/{id}` | Пометить адаптер активным |
| GET | `/api/style/profile` | Текущий профиль стиля |
| PUT | `/api/style/profile` | Ручное редактирование |
| POST | `/api/style/reanalyze` | Пересобрать профиль |
| GET | `/api/stream/events` | SSE входящих сообщений |
| GET | `/api/whisper/status` | Статус Whisper, VRAM |
| POST | `/api/whisper/transcribe` | Транскрипция сообщения |
| POST | `/api/whisper/unload` | Выгрузить Whisper из VRAM |
| GET/POST | `/api/settings/whisper` | Настройки Whisper |
| GET | `/api/stats/voice` | Статистика по голосовым |
| POST | `/api/webhook/set` | Зарегистрировать webhook |
| POST | `/webhook/{token}` | Telegram webhook |

Полный список — в Swagger: <http://localhost:8000/docs>.

---

# Частые проблемы

| Симптом | Решение |
| --- | --- |
| `CUDA is not available` при обучении | Переустанови CUDA-сборку torch (см. шаг 2) |
| Бот не получает сообщения | Туннель не поднялся / webhook не зарегистрирован — проверь логи и `/api/webhook/set` |
| `cloudflared` quick-tunnel падает | Временный 500 на trycloudflare.com — перезапусти `start.py` или используй ngrok |
| Whisper не запускается | Установи ffmpeg в PATH, перезапусти терминал |
| Кириллица в логах ломается | `start.py` форсит UTF-8; запускай через него, а не напрямую |
| `start.py` не находит venv | Создай `.venv` в корне проекта (`py -3.11 -m venv .venv`) |

---

# Лицензия

MIT
