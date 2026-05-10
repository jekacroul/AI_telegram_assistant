"""Application configuration loaded from environment variables."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv, set_key
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT_DIR / ".env"

if ENV_PATH.exists():
    load_dotenv(ENV_PATH)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ENV_PATH), extra="ignore")

    telegram_api_id: str = ""
    telegram_api_hash: str = ""
    telegram_phone: str = ""
    telegram_session_name: str = "telegram_local_ai"

    ollama_host: str = "http://127.0.0.1:11434"
    ollama_model: str = "mistral:7b"

    database_url: str = "sqlite+aiosqlite:///./telegram_local_ai.db"

    api_host: str = "127.0.0.1"
    api_port: int = 8000

    hf_base_model: str = "mistralai/Mistral-7B-Instruct-v0.3"
    training_output_dir: str = str(ROOT_DIR / "models")
    training_data_dir: str = str(ROOT_DIR / "training_data")
    min_pairs_for_training: int = 50
    use_4bit: bool = True


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def update_env(updates: dict[str, str]) -> None:
    """Persist updates to .env and refresh the settings cache."""
    if not ENV_PATH.exists():
        ENV_PATH.touch()
    for key, value in updates.items():
        set_key(str(ENV_PATH), key, value)
        os.environ[key] = value
    get_settings.cache_clear()
