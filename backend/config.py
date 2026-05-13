from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    openai_base_url: str = Field(
        default="http://localhost:1234/v1", alias="OPENAI_BASE_URL"
    )
    openai_api_key: str = Field(default="local", alias="OPENAI_API_KEY")
    openai_model: str = Field(default="local-model", alias="OPENAI_MODEL")
    openai_transcription_model: str = Field(
        default="whisper-1", alias="OPENAI_TRANSCRIPTION_MODEL"
    )
    auto_reply: bool = Field(default=False, alias="AUTO_REPLY")
    db_path: str = Field(default="./data/database.db", alias="DB_PATH")
    training_data_path: str = Field(default="./training_data/", alias="TRAINING_DATA_PATH")
    models_path: str = Field(default="./models/", alias="MODELS_PATH")
    logs_path: str = Field(default="./logs/", alias="LOGS_PATH")
    webhook_base_url: Optional[str] = Field(default=None, alias="WEBHOOK_BASE_URL")
    user_name: str = Field(default="Я", alias="USER_NAME")
    hf_base_model: str = Field(default="mistralai/Mistral-7B-Instruct-v0.2", alias="HF_BASE_MODEL")
    llm_max_tokens: int = Field(default=2048, alias="LLM_MAX_TOKENS")
    llama_cpp_path: Optional[str] = Field(default=None, alias="LLAMA_CPP_PATH")
    lm_studio_models_dir: Optional[str] = Field(default=None, alias="LM_STUDIO_MODELS_DIR")
    gguf_quant: str = Field(default="Q8_0", alias="GGUF_QUANT")
    notify_chat_id: str = Field(default="", alias="NOTIFY_CHAT_ID")
    media_path: str = Field(default="./media/", alias="MEDIA_PATH")

    @property
    def db_url(self) -> str:
        path = Path(self.db_path)
        if not path.is_absolute():
            path = ROOT_DIR / path
        path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite+aiosqlite:///{path}"

    @property
    def training_data_dir(self) -> Path:
        path = Path(self.training_data_path)
        if not path.is_absolute():
            path = ROOT_DIR / path
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def models_dir(self) -> Path:
        path = Path(self.models_path)
        if not path.is_absolute():
            path = ROOT_DIR / path
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def logs_dir(self) -> Path:
        path = Path(self.logs_path)
        if not path.is_absolute():
            path = ROOT_DIR / path
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def media_dir(self) -> Path:
        path = Path(self.media_path)
        if not path.is_absolute():
            path = ROOT_DIR / path
        path.mkdir(parents=True, exist_ok=True)
        return path


settings = Settings()
