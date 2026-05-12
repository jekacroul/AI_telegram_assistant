from __future__ import annotations

import re
from typing import Any

import emoji

AI_PHRASE_PREFIXES = (
    "Как языковая модель",
    "Я не могу",
    "As an AI",
)

_WORD_RE = re.compile(r"[\wА-Яа-яЁё]+", re.UNICODE)
_CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_ALNUM_RE = re.compile(r"[\wА-Яа-яЁё]", re.UNICODE)


def _word_count(text: str) -> int:
    return len(_WORD_RE.findall(text or ""))


def _detect_language(text: str) -> str:
    """Return a coarse language code for short Telegram messages.

    The filter only needs to avoid obvious cross-language replies. Very short
    messages without Cyrillic/Latin letters are treated as unknown and pass the
    language check.
    """
    cyrillic = len(_CYRILLIC_RE.findall(text or ""))
    latin = len(_LATIN_RE.findall(text or ""))
    if cyrillic == 0 and latin == 0:
        return "unknown"
    if cyrillic >= latin:
        return "ru"
    return "en"


def _is_emoji_only(text: str) -> bool:
    stripped = (text or "").strip()
    if not stripped:
        return False
    if emoji.emoji_count(stripped) <= 0:
        return False
    without_emoji = emoji.replace_emoji(stripped, replace="")
    return not _ALNUM_RE.search(without_emoji)


def _uses_emoji(style_profile: dict[str, Any] | None) -> bool:
    if not isinstance(style_profile, dict):
        return True
    return bool(style_profile.get("uses_emoji", True))


def is_good_response(
    text: str,
    incoming_text: str,
    style_profile: dict[str, Any] | None,
) -> tuple[bool, str]:
    """Validate an LLM reply candidate before it is shown or sent.

    Returns (True, "ok") for acceptable responses, otherwise (False, reason).
    """
    candidate = (text or "").strip()
    incoming = (incoming_text or "").strip()

    if _word_count(candidate) < 2:
        return False, "too_short"

    incoming_lang = _detect_language(incoming)
    candidate_lang = _detect_language(candidate)
    if (
        incoming_lang != "unknown"
        and candidate_lang != "unknown"
        and incoming_lang != candidate_lang
    ):
        return False, "language_mismatch"

    if incoming and candidate.casefold() == incoming.casefold():
        return False, "identical_to_incoming"

    for prefix in AI_PHRASE_PREFIXES:
        if candidate.casefold().startswith(prefix.casefold()):
            return False, "ai_phrase"

    if not _uses_emoji(style_profile) and _is_emoji_only(candidate):
        return False, "emoji_only"

    return True, "ok"
