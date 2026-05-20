from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT_DIR = Path(__file__).resolve().parent.parent

# Keep every downloaded model inside the project folder instead of the
# system-wide caches. Set before sentence-transformers / transformers are
# ever imported so their loaders pick these locations up.
_HF_HOME = ROOT_DIR / "models" / "huggingface"
_ST_HOME = ROOT_DIR / "models" / "sentence_transformers"
for _path in (_HF_HOME, _ST_HOME):
    _path.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("HF_HOME", str(_HF_HOME))
os.environ.setdefault("SENTENCE_TRANSFORMERS_HOME", str(_ST_HOME))


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
    auto_reply: bool = Field(default=False, alias="AUTO_REPLY")
    db_path: str = Field(default="./data/database.db", alias="DB_PATH")
    training_data_path: str = Field(default="./training_data/", alias="TRAINING_DATA_PATH")
    models_path: str = Field(default="./models/", alias="MODELS_PATH")
    logs_path: str = Field(default="./logs/", alias="LOGS_PATH")
    webhook_base_url: Optional[str] = Field(default=None, alias="WEBHOOK_BASE_URL")
    user_name: str = Field(default="Я", alias="USER_NAME")
    user_display_name: str = Field(default="", alias="USER_DISPLAY_NAME")
    hf_base_model: str = Field(default="mistralai/Mistral-7B-Instruct-v0.2", alias="HF_BASE_MODEL")
    llm_max_tokens: int = Field(default=2048, alias="LLM_MAX_TOKENS")
    llama_cpp_path: Optional[str] = Field(default=None, alias="LLAMA_CPP_PATH")
    gguf_quant: str = Field(default="Q8_0", alias="GGUF_QUANT")
    llama_server_bin: Optional[str] = Field(default=None, alias="LLAMA_SERVER_BIN")
    llama_base_model_gguf: Optional[str] = Field(
        default=None, alias="LLAMA_BASE_MODEL_GGUF"
    )
    llama_server_port: int = Field(default=1234, alias="LLAMA_SERVER_PORT")
    llama_server_ngl: int = Field(default=99, alias="LLAMA_SERVER_NGL")
    llama_server_ctx: int = Field(default=4096, alias="LLAMA_SERVER_CTX")
    llama_server_auto_start: bool = Field(
        default=True, alias="LLAMA_SERVER_AUTO_START"
    )
    media_path: str = Field(default="./media/", alias="MEDIA_PATH")
    owner_chat_id: str = Field(default="", alias="OWNER_CHAT_ID")
    admin_bot_enabled: bool = Field(default=True, alias="ADMIN_BOT_ENABLED")
    caldav_url: str = Field(
        default="https://caldav.icloud.com", alias="CALDAV_URL"
    )
    caldav_username: str = Field(default="", alias="CALDAV_USERNAME")
    caldav_password: str = Field(default="", alias="CALDAV_PASSWORD")
    caldav_enabled: bool = Field(default=False, alias="CALDAV_ENABLED")
    caldav_calendar_name: str = Field(default="", alias="CALDAV_CALENDAR_NAME")
    caldav_lookahead_days: int = Field(default=7, alias="CALDAV_LOOKAHEAD_DAYS")
    caldav_slot_duration: int = Field(default=60, alias="CALDAV_SLOT_DURATION")
    caldav_work_start: int = Field(default=9, alias="CALDAV_WORK_START")
    caldav_work_end: int = Field(default=20, alias="CALDAV_WORK_END")

    @property
    def display_name(self) -> str:
        """Short name shown in the web UI (Telegram-style). Falls back to the
        full chat name when USER_DISPLAY_NAME is not set."""
        return self.user_display_name.strip() or self.user_name

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

    @property
    def hf_home_dir(self) -> Path:
        _HF_HOME.mkdir(parents=True, exist_ok=True)
        return _HF_HOME

    @property
    def sentence_transformers_dir(self) -> Path:
        _ST_HOME.mkdir(parents=True, exist_ok=True)
        return _ST_HOME

    @property
    def chroma_db_dir(self) -> Path:
        path = ROOT_DIR / "data" / "chroma_db"
        path.mkdir(parents=True, exist_ok=True)
        return path


settings = Settings()
