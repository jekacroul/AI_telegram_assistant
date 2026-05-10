# Telegram Local AI Assistant

Полностью локальный Telegram-ассистент, который учится твоему стилю общения через
LoRA fine-tuning локального LLM (Mistral 7B / LLaMA 3.1 8B). Никаких внешних API.

## Стек

- **LLM**: [Ollama](https://ollama.com) (`mistral:7b` по умолчанию)
- **Fine-tuning**: PEFT + transformers + bitsandbytes (4-bit NF4) + TRL/SFTTrainer
- **Telegram**: aiogram 3.x (Bot API, async). Подключается через Telegram Premium
  «Chat Automation» — бот отвечает от твоего имени.
- **Backend**: FastAPI + SQLAlchemy (async) + SQLite + SSE
- **Frontend**: React + Vite + Tailwind
- **GPU**: NVIDIA 8 GB+, `device_map="cuda:0"`, flash-attn 2 (если доступен)

## Структура

```
telegram-local-ai/
├── backend/
│   ├── main.py               # FastAPI + lifespan
│   ├── bot.py                # aiogram bot, обработчик webhook
│   ├── llm_engine.py         # Ollama inference
│   ├── trainer.py            # LoRA fine-tuning pipeline
│   ├── dataset_builder.py    # Сбор обучающих пар
│   ├── style_engine.py       # Анализ стиля
│   ├── database.py           # SQLAlchemy модели
│   └── config.py             # Настройки из .env
├── frontend/
│   └── src/
│       ├── pages/            # Dashboard, Training, StyleProfile, Settings
│       └── components/       # MessageCard, ReplyModal, StatusDot
├── training_data/            # JSONL датасеты
├── models/                   # LoRA адаптеры
├── .env.example
├── requirements.txt
└── README.md
```

## Быстрый старт

### 1. Ollama

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama serve &
ollama pull mistral:7b
```

### 2. Backend

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env .env
# отредактируй .env: TELEGRAM_BOT_TOKEN, USER_NAME, при желании HF_BASE_MODEL
uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

### 3. Frontend

```bash
cd frontend
npm install
npm run dev      # http://localhost:5173 (проксирует /api на :8000)
# или для прода:
npm run build    # backend сам отдаст dist на /
```

### 4. Webhook

Бот работает через webhook. После старта backend:

```
POST /api/webhook/set { "url": "https://your-public-host/webhook/<TOKEN>" }
```

Для локальной разработки можно использовать [ngrok](https://ngrok.com).

## Как это работает

1. **Сбор данных**. Каждое входящее и исходящее сообщение сохраняется в SQLite.
2. **Анализ стиля** (`style_engine`). Каждые 20 новых сообщений автоматически
   пересобирается профиль: средняя длина, эмодзи, тон, частые слова, приветствия.
3. **Генерация ответа** (`llm_engine`). На входящее сообщение Ollama выдаёт
   3 варианта ответа в JSON, с системным промптом, в который инжектится
   профиль стиля и история чата.
4. **Режимы**:
   - `AUTO_REPLY=true` — бот отвечает сам, ты ставишь 👍/👎 и правишь.
   - `AUTO_REPLY=false` — варианты приходят на дашборд, ты выбираешь.
5. **Fine-tuning** (`trainer`). Когда накопилось ≥ 50 пар (input → output),
   запускаешь LoRA-обучение: r=16, alpha=32, target=`q_proj,v_proj`,
   3 эпохи, 4-bit NF4, fp16 compute. По завершении адаптер сохраняется
   в `models/lora_adapter_v{N}/`, и пишется `Modelfile` для Ollama, который
   можно «горячо» подгрузить через `/api/training/activate/{run_id}`.
6. **Обратная связь**. 👎 в режиме авто-ответа открывает редактор: правишь,
   re-send, правильная пара уходит в `training_pairs` со `feedback=bad/good`.

## API (выжимка)

| Метод | Путь | Описание |
| --- | --- | --- |
| GET | `/api/status` | Здоровье Ollama / Bot / DB |
| GET | `/api/chats` | Список чатов |
| GET/POST | `/api/settings` | Токен, авто-ответ, мониторинг |
| GET | `/api/messages/pending` | Очередь на ручной ответ |
| POST | `/api/reply/generate` | 3 варианта для message_id |
| POST | `/api/reply/approve` | Одобрить и отправить |
| POST | `/api/reply/feedback` | 👍/👎 + правка |
| GET | `/api/training/status` | Статистика и активный адаптер |
| POST | `/api/training/build-dataset` | Собрать JSONL |
| POST | `/api/training/start` | Запустить обучение |
| GET | `/api/training/progress` | SSE: epoch/step/loss/eta |
| POST | `/api/training/activate/{id}` | Сделать адаптер активным в Ollama |
| GET | `/api/style/profile` | Текущий профиль |
| PUT | `/api/style/profile` | Ручное редактирование |
| POST | `/api/style/reanalyze` | Пересобрать |
| GET | `/api/stream/events` | SSE входящих сообщений |
| POST | `/webhook/{token}` | Telegram webhook |

## .env

```
TELEGRAM_BOT_TOKEN=
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=mistral:7b
AUTO_REPLY=false
DB_PATH=./data/database.db
TRAINING_DATA_PATH=./training_data/
MODELS_PATH=./models/
USER_NAME=Я
HF_BASE_MODEL=mistralai/Mistral-7B-Instruct-v0.2
```

## Лицензия

MIT
