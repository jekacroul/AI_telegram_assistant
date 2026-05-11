from __future__ import annotations

import logging
from typing import Optional

from .database import SessionLocal, get_setting

log = logging.getLogger(__name__)


SETTING_NOTIFY_CHAT_ID = "notify_chat_id"
SETTING_NOTIFY_ENABLED = "notify_enabled"
SETTING_LAST_PRIVATE_CHAT_ID = "last_private_chat_id"


def _truncate(text: str, limit: int = 500) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


async def notify_owner(
    chat_name: str,
    sender_name: str,
    original_text: str,
    reply_sent: str,
) -> None:
    """Send a notification to the owner about an auto-reply.

    Silently skips if NOTIFY_CHAT_ID is empty or notifications are disabled.
    """
    from .bot import telegram_service

    try:
        async with SessionLocal() as session:
            notify_chat_id = await get_setting(session, SETTING_NOTIFY_CHAT_ID, "")
            enabled = await get_setting(session, SETTING_NOTIFY_ENABLED, "1") in (
                "1", "true", "True",
            )
    except Exception:  # noqa: BLE001
        log.exception("notify_owner: cannot read settings")
        return

    if not notify_chat_id or not enabled:
        return

    if not telegram_service.is_configured or telegram_service.bot is None:
        return

    try:
        target = int(notify_chat_id)
    except (TypeError, ValueError):
        log.warning("notify_owner: invalid NOTIFY_CHAT_ID=%r", notify_chat_id)
        return

    text = (
        f"🤖 Ответил в чате {chat_name}\n"
        f"👤 {sender_name}: {_truncate(original_text)}\n"
        f"✉️ Мой ответ: {_truncate(reply_sent)}"
    )

    try:
        await telegram_service.bot.send_message(target, text)
    except Exception as e:  # noqa: BLE001
        log.warning("notify_owner: failed to send notification: %s", e)
