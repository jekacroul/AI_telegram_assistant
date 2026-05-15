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

### 4. ffmpeg (для голосовых)

Транскрипция голосовых через Whisper требует ffmpeg в PATH:

```bash
# Windows
winget install ffmpeg
# перезапусти терминал после установки

# проверка
ffmpeg -version
```

Проверить что openai-whisper установлен:

```bash
py -3.11 -c "import whisper; print(whisper.available_models())"
```

### 5. Webhook

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
   квантизуется в один GGUF (`GGUF_QUANT`, по умолчанию `Q8_0`),
   результат остаётся в той же `lora_adapter_v{N}/` папке.
   `/api/training/activate/{run_id}` помечает адаптер активным в БД.
6. **Обратная связь**. 👎 в режиме авто-ответа открывает редактор: правишь,
   re-send, правильная пара уходит в `training_pairs` со `feedback=bad/good`.

## Голосовые сообщения

Бот умеет принимать голосовые/аудио сообщения и транскрибировать их локально
через [openai-whisper](https://github.com/openai/whisper). По умолчанию
используется модель `large-v3` с языком `ru` — она даёт лучшее качество для
русского.

VRAM (приблизительно):

- `tiny`     — ~1 GB
- `base`     — ~1 GB
- `small`    — ~1.5 GB
- `medium`   — ~3 GB (хороший компромисс)
- `large-v3` — ~6 GB

Если основная LLM крупная (например saiga 12B Q8 ≈13 GB) и видеокарта на 12 GB,
включи в Settings → «Голосовые сообщения» опцию *«Освобождать VRAM после
транскрипции»* (lazy_load): Whisper будет загружаться перед обработкой и
выгружаться сразу после, освобождая память для LLM.

В Settings → «Голосовые сообщения» можно выбрать как бот реагирует на
голосовые:

- *Отвечать текстом автоматически* — стандартный режим: транскрипция →
  генерация ответа → отправка.
- *Добавлять в очередь для ручного ответа* — голосовые попадают в pending,
  ответы готовишь сам.
- *Игнорировать голосовые* — бот их пропускает.

Низкая уверенность транскрипции (avg confidence < 0.5) автоматически
переводит сообщение в pending, чтобы можно было поправить текст вручную
перед генерацией.

## Векторная память (RAG)

Бот не ограничивается последними 8 сообщениями: каждое сообщение
векторизуется и складывается в локальную базу [ChromaDB](https://www.trychroma.com/),
а при генерации ответа бот находит семантически близкие сообщения по всей
истории переписки. Эмбеддинги считаются моделью
`paraphrase-multilingual-mpnet-base-v2` (лучшая мультиязычная модель для
русского). Всё работает локально, ничего не уходит наружу.

Установка и проверка:

```bash
py -3.11 -m pip install chromadb sentence-transformers
```

Все модели скачиваются в папку проекта (`models/huggingface`,
`models/sentence_transformers`), а векторная база — в `data/chroma_db/`.
Примерный размер базы: ~40 КБ на 1000 сообщений.

Первый запуск — автоматическая индексация: при старте бот проиндексирует
все существующие сообщения. Это занимает ~1–5 минут в зависимости от
размера истории, прогресс виден в логах: `RAG: индексирую 450/1200...`.
Новые сообщения индексируются в фоне, не задерживая ответ.

Тест поиска: Training → «Векторная память» → «Тест поиска» — введи любую
фразу и посмотри, какой контекст находит бот.

Настройка чувствительности — Settings → «Векторная память (RAG)»:
порог схожести, число результатов и поиск по всем чатам.

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
| GET | `/api/whisper/status` | Статус модели Whisper, VRAM |
| POST | `/api/whisper/transcribe` | Транскрипция конкретного сообщения |
| POST | `/api/whisper/retranscribe` | Перегенерация транскрипции |
| POST | `/api/whisper/unload` | Выгрузить Whisper из VRAM |
| GET/POST | `/api/settings/whisper` | Настройки Whisper и режима ответа |
| GET | `/api/stats/voice` | Статистика по голосовым |
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

# Авто-экспорт обученной модели в GGUF. Необязательно.
# LLAMA_CPP_PATH — путь к собранному клону https://github.com/ggerganov/llama.cpp
# (нужны convert_hf_to_gguf.py и собранный llama-quantize в build/bin/).
# GGUF_QUANT — тип квантизации (Q4_K_M / Q5_K_M / Q8_0 / F16). По умолчанию Q8_0.
LLAMA_CPP_PATH=
GGUF_QUANT=Q8_0
```

## Лицензия

MIT
