from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Awaitable, Callable, Optional

import emoji
from sqlalchemy.ext.asyncio import AsyncSession

from .database import QualityLog, get_setting, set_setting

SETTING_TOTAL_GENERATED = "quality_total_generated"

log = logging.getLogger(__name__)


_AI_PHRASES = (
    "как языковая модель",
    "как ai",
    "как ии",
    "я не могу",
    "я как ai",
    "as an ai",
    "as a language model",
    "i'm an ai",
    "i am an ai",
    "i cannot",
    "i can't help",
)

_CYRILLIC_RE = re.compile(r"[а-яА-ЯёЁ]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_WORD_RE = re.compile(r"[\wа-яА-ЯёЁ]+", re.UNICODE)


def _detect_language(text: str) -> str:
    """Return 'ru', 'en' or 'other' based on dominant script."""
    if not text:
        return "other"
    cyr = len(_CYRILLIC_RE.findall(text))
    lat = len(_LATIN_RE.findall(text))
    if cyr == 0 and lat == 0:
        return "other"
    if cyr >= lat:
        return "ru"
    return "en"


def _strip_to_visible(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _is_emoji_only(text: str) -> bool:
    stripped = _strip_to_visible(text)
    if not stripped:
        return False
    for ch in stripped:
        if ch.isspace():
            continue
        if ch in emoji.EMOJI_DATA:
            continue
        return False
    return True


def is_good_response(
    text: str,
    incoming_text: str,
    style_profile: Optional[dict],
) -> tuple[bool, str]:
    """Validate an LLM-generated response.

    Returns (ok, reason). reason is empty when ok is True.
    """
    cleaned = _strip_to_visible(text)
    if not cleaned:
        return False, "empty"

    uses_emoji = bool((style_profile or {}).get("uses_emoji", True))
    if not uses_emoji and _is_emoji_only(cleaned):
        return False, "emoji_only"

    words = _WORD_RE.findall(cleaned)
    if len(words) < 2:
        return False, "too_short"

    incoming_clean = _strip_to_visible(incoming_text)
    if incoming_clean and cleaned.lower() == incoming_clean.lower():
        return False, "echoes_incoming"

    lowered = cleaned.lower()
    for phrase in _AI_PHRASES:
        if lowered.startswith(phrase):
            return False, "ai_phrase"

    incoming_lang = _detect_language(incoming_clean)
    response_lang = _detect_language(cleaned)
    if (
        incoming_lang in ("ru", "en")
        and response_lang in ("ru", "en")
        and incoming_lang != response_lang
    ):
        return False, "language_mismatch"

    return True, ""


async def log_rejection(
    session: AsyncSession,
    reason: str,
    incoming_text: str,
    rejected_text: str,
) -> None:
    try:
        session.add(QualityLog(
            timestamp=datetime.utcnow(),
            reason=reason,
            incoming_text=incoming_text or "",
            rejected_text=rejected_text or "",
        ))
        await session.commit()
    except Exception:  # noqa: BLE001
        log.exception("failed to log quality rejection")


async def _increment_total_generated(session: AsyncSession, count: int) -> None:
    if count <= 0:
        return
    try:
        current = await get_setting(session, SETTING_TOTAL_GENERATED, "0")
        try:
            value = int(current or "0")
        except ValueError:
            value = 0
        await set_setting(session, SETTING_TOTAL_GENERATED, str(value + count))
    except Exception:  # noqa: BLE001
        log.exception("failed to increment quality_total_generated")


async def filter_variants(
    variants: list[str],
    incoming_text: str,
    style_profile: Optional[dict],
    session: AsyncSession,
) -> tuple[list[str], list[tuple[str, str]]]:
    """Split variants into accepted and rejected (with reasons).

    Rejections are persisted to the quality_log table.
    """
    accepted: list[str] = []
    rejected: list[tuple[str, str]] = []
    for variant in variants:
        ok, reason = is_good_response(variant, incoming_text, style_profile)
        if ok:
            accepted.append(variant)
        else:
            rejected.append((variant, reason))
            await log_rejection(session, reason, incoming_text, variant)
    await _increment_total_generated(session, len(variants))
    return accepted, rejected


async def generate_with_filter(
    generator: Callable[[], Awaitable[list[str]]],
    incoming_text: str,
    style_profile: Optional[dict],
    session: AsyncSession,
    max_attempts: int = 3,
) -> tuple[list[str], list[tuple[str, str]]]:
    """Call ``generator`` up to ``max_attempts`` times until at least one
    variant passes the quality filter.

    Returns (accepted, rejected). ``accepted`` is empty when all attempts
    produced only rejected variants — in that case the caller should fall
    back to the pending queue.
    """
    all_rejected: list[tuple[str, str]] = []
    attempts = max(1, int(max_attempts))
    for attempt in range(attempts):
        try:
            variants = await generator()
        except Exception:
            raise
        accepted, rejected = await filter_variants(
            variants, incoming_text, style_profile, session
        )
        all_rejected.extend(rejected)
        if accepted:
            return accepted, all_rejected
        log.info(
            "quality filter: attempt %d/%d produced %d rejected variants",
            attempt + 1,
            attempts,
            len(rejected),
        )
    return [], all_rejected
