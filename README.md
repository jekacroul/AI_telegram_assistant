# AI_telegram_assistant

A fully local Telegram assistant that learns your writing style. Uses **Ollama**
for inference and **Hugging Face PEFT** for LoRA fine-tuning of the base model
on your own message history. No external API calls are made — everything runs
on your machine.

## Stack
- Backend: FastAPI + SQLAlchemy (SQLite, async)
- Telegram: Telethon userbot
- LLM inference: Ollama (`mistral:7b` by default — strong Russian)
- Fine-tuning: PEFT + transformers + bitsandbytes (4-bit on GPU, fp32 fallback on CPU)
- Frontend: React + Vite + Tailwind

## Project layout
```
backend/
  main.py            FastAPI app + endpoints
  telegram_client.py Telethon userbot
  llm_engine.py      Ollama integration
  trainer.py         LoRA fine-tuning pipeline
  dataset_builder.py Build training pairs from messages
  style_engine.py    Style profile analysis
  database.py        SQLAlchemy models + async session
  config.py          Pydantic settings (.env)
frontend/src/
  pages/             Dashboard, Training, StyleProfile, Settings
  components/        Card, Button, ReplyModal …
training_data/       Auto-generated JSONL datasets
models/              Saved LoRA adapters
```

## Quickstart
1. Install Ollama and pull the model:
   ```bash
   ollama pull mistral:7b
   ```
2. Python backend:
   ```bash
   python -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   cp .env.example .env  # fill TELEGRAM_API_ID/HASH/PHONE
   python -m backend.telegram_login   # interactive Telegram authorization
   uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
   ```
3. Frontend:
   ```bash
   cd frontend
   npm install
   npm run dev
   # open http://localhost:5173
   ```

## Workflow
1. **Settings** → enter Telegram API credentials, choose Ollama model, save.
2. Run `python -m backend.telegram_login` once to authorize the userbot.
3. **Settings → Monitored chats** → tick the chats you want to learn from and reply in;
   "Apply & import" pulls history into SQLite.
4. **Style profile → Reanalyze** to build the JSON style profile.
5. **Dashboard** → incoming messages stream in real time. Click → modal shows 3
   AI-drafted variants. Edit inline → Send. Sent replies are auto-saved as
   approved training pairs.
6. **Training** → "Build dataset" → "Start fine-tuning" once you have at least
   50 pairs. Watch live loss / progress. Activate any adapter version after it
   finishes.

## Notes
- All LLM calls hit your local Ollama at `OLLAMA_HOST` — no external services.
- LoRA fine-tuning runs in a background asyncio task; cancel it from the UI.
- `models/lora_adapter_v{N}/` contains adapter weights + `adapter_meta.json`.
  Loading the adapter into Ollama itself requires producing a Modelfile from
  the merged weights — out of scope for this MVP, but the trainer stores
  everything you need.
