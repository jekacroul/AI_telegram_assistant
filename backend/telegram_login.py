"""Interactive Telegram session bootstrap.

Run once before launching the FastAPI server so Telethon can store a session
file in the project root:

    python -m backend.telegram_login
"""
from __future__ import annotations

import asyncio

from telethon import TelegramClient

from .config import get_settings


async def _main() -> None:
    settings = get_settings()
    if not (settings.telegram_api_id and settings.telegram_api_hash and settings.telegram_phone):
        raise SystemExit(
            "TELEGRAM_API_ID, TELEGRAM_API_HASH and TELEGRAM_PHONE must be set in .env"
        )
    client = TelegramClient(
        settings.telegram_session_name,
        int(settings.telegram_api_id),
        settings.telegram_api_hash,
    )
    await client.start(phone=settings.telegram_phone)
    me = await client.get_me()
    print(f"Authorized as {me.username or me.id}")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(_main())
