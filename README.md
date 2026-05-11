# Telegram Local AI Assistant

Полностью локальный Telegram-ассистент, который учится твоему стилю общения через
LoRA fine-tuning локального LLM (Mistral 7B / LLaMA 3.1 8B). Никаких внешних API.

## Стек

- **LLM**: локальный OpenAI-совместимый сервер ([LM Studio](https://lmstudio.ai),
  llama.cpp server, vLLM и т.п.)
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
│   ├── llm_engine.py         # OpenAI-compatible LLM client
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

### 1. LM Studio

Скачай [LM Studio](https://lmstudio.ai), загрузи нужную модель и запусти
встроенный OpenAI-совместимый сервер (по умолчанию `http://localhost:1234/v1`).
Любой другой OpenAI-совместимый бэкенд (llama.cpp server, vLLM) тоже подойдёт —
укажи его URL в `OPENAI_BASE_URL`.

### 2. Backend

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env .env
# отредактируй .env: TELEGRAM_BOT_TOKEN, USER_NAME, при желании HF_BASE_MODEL
uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

> ⚠️ Для LoRA-обучения нужна CUDA-сборка PyTorch. Дефолтный wheel из
> `requirements.txt` на Windows ставится в CPU-only режиме, и обучение упадёт
> с `CUDA is not available`. Переустанови torch под свою версию драйвера:
>
> ```bash
> pip uninstall -y torch torchvision torchaudio
> pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
> ```
>
> (или `cu124` / `cu118` под нужную версию). Проверь:
> `python -c "import torch; print(torch.cuda.is_available(), torch.version.cuda)"`.

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
3. **Генерация ответа** (`llm_engine`). На входящее сообщение локальный LLM
   выдаёт 3 варианта ответа, с системным промптом, в который инжектится
   профиль стиля и история чата.
4. **Режимы**:
   - `AUTO_REPLY=true` — бот отвечает сам, ты ставишь 👍/👎 и правишь.
   - `AUTO_REPLY=false` — варианты приходят на дашборд, ты выбираешь.
5. **Fine-tuning** (`trainer`). Когда накопилось ≥ 50 пар (input → output),
   запускаешь LoRA-обучение: r=16, alpha=32, target=`q_proj,v_proj`,
   3 эпохи, 4-bit NF4, fp16 compute. По завершении адаптер сохраняется
   в `models/lora_adapter_v{N}/`. Если в `.env` указан `LLAMA_CPP_PATH`,
   адаптер автоматически **сливается с базовой моделью** в fp16 и
   квантизуется в один GGUF (`GGUF_QUANT`, по умолчанию `Q8_0`). При
   указанном `LM_STUDIO_MODELS_DIR` итоговый файл копируется прямо в
   папку моделей LM Studio — грузишь как обычную модель, без адаптеров.
   `/api/training/activate/{run_id}` помечает адаптер активным в БД.
6. **Обратная связь**. 👎 в режиме авто-ответа открывает редактор: правишь,
   re-send, правильная пара уходит в `training_pairs` со `feedback=bad/good`.

## API (выжимка)

| Метод | Путь | Описание |
| --- | --- | --- |
| GET | `/api/status` | Здоровье LLM / Bot / DB |
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
| POST | `/api/training/activate/{id}` | Пометить адаптер активным в БД |
| GET | `/api/style/profile` | Текущий профиль |
| PUT | `/api/style/profile` | Ручное редактирование |
| POST | `/api/style/reanalyze` | Пересобрать |
| GET | `/api/stream/events` | SSE входящих сообщений |
| POST | `/webhook/{token}` | Telegram webhook |

## .env

```
TELEGRAM_BOT_TOKEN=
OPENAI_BASE_URL=http://localhost:1234/v1
OPENAI_API_KEY=local
OPENAI_MODEL=local-model
LLM_MAX_TOKENS=2048
AUTO_REPLY=false
DB_PATH=./data/database.db
TRAINING_DATA_PATH=./training_data/
MODELS_PATH=./models/
USER_NAME=Я
HF_BASE_MODEL=mistralai/Mistral-7B-Instruct-v0.2

# Авто-экспорт обученной модели в GGUF для LM Studio. Необязательно.
# LLAMA_CPP_PATH — путь к собранному клону https://github.com/ggerganov/llama.cpp
# (нужны convert_hf_to_gguf.py и собранный llama-quantize в build/bin/).
# LM_STUDIO_MODELS_DIR — папка моделей LM Studio; туда копируется итоговый GGUF.
# GGUF_QUANT — тип квантизации (Q4_K_M / Q5_K_M / Q8_0 / F16). По умолчанию Q8_0.
LLAMA_CPP_PATH=
LM_STUDIO_MODELS_DIR=
GGUF_QUANT=Q8_0
```

## Лицензия

MIT
