from __future__ import annotations

import json
import logging
import random
import re
from typing import Optional

import httpx

from .config import settings

log = logging.getLogger(__name__)


_SENTENCE_END_CHARS = set('.!?…:;)]}»"\'`')
_EMOJI_RANGES = (
    (0x2600, 0x27BF),
    (0x1F300, 0x1FAFF),
)


def _looks_complete(variant: str) -> bool:
    s = (variant or "").rstrip()
    if not s:
        return False
    last = s[-1]
    if last in _SENTENCE_END_CHARS:
        return True
    code = ord(last)
    return any(lo <= code <= hi for lo, hi in _EMOJI_RANGES)


def pick_auto_variant(variants: list[str]) -> Optional[str]:
    """Pick a variant for auto-reply, preferring ones that don't look truncated."""
    if not variants:
        return None
    complete = [v for v in variants if _looks_complete(v)]
    if complete:
        return random.choice(complete)
    log.warning("all %d LLM variants look truncated; falling back", len(variants))
    return random.choice(variants)


class LLMUnavailableError(RuntimeError):
    pass


# Output budget for a chat reply. Kept small so prompt + completion stay
# well inside the model's context window (a chat reply needs little room).
REPLY_MAX_TOKENS = 400


SYSTEM_TEMPLATE = (
    "Ты пишешь сообщения в Telegram от первого лица — это твоя личная"
    " переписка, ты отвечаешь сам за себя. Говори о себе только в первом"
    " лице: 'я', 'мне', 'у меня', 'мой'. Категорически нельзя называть"
    " себя по имени и говорить о себе в третьем лице, как о постороннем"
    " человеке. Ты не бот, не ассистент и не ИИ — не упоминай это.\n"
    "Отвечай только текстом сообщения, как в живом чате: без подписи"
    " своим именем, без JSON, без кавычек, без нумерованных списков,"
    " заголовков и пояснений. Просто одно живое сообщение-ответ.\n"
    "Если собеседник прямо спросит, как тебя зовут — ответь: {user_name}"
    " (точно в этом написании, буква в букву, без сокращений и выдуманных"
    " отчеств). Если тебе приписывают чужое имя или факты — вежливо, но"
    " твёрдо поправь.\n"
    "Стиль речи: {style_profile}\n"
    "{summary_block}"
    "{rag_block}"
    "Ниже переписка с собеседником {sender_name}: его реплики — роль"
    " user, твои собственные ответы — роль assistant. Ответь на последнее"
    " сообщение от первого лица, опираясь на весь предыдущий диалог."
)


_DAYS_RU_FULL = [
    "Понедельник", "Вторник", "Среда", "Четверг",
    "Пятница", "Суббота", "Воскресенье",
]
_MONTHS_RU_GEN = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


def _current_date_block() -> str:
    """A short, explicit anchor for the model: today's weekday, date and
    local time. The model needs this to interpret relative references like
    "сегодня", "завтра", "до конца недели", "в пятницу"."""
    from datetime import datetime

    now = datetime.now()
    return (
        "Текущая дата и время: "
        f"{_DAYS_RU_FULL[now.weekday()]}, "
        f"{now.day} {_MONTHS_RU_GEN[now.month - 1]} {now.year} г., "
        f"{now.strftime('%H:%M')}. "
        "Опирайся на эту дату, когда собеседник говорит «сегодня», «завтра»,"
        " «послезавтра», «в пятницу», «до конца недели», «на этой неделе»"
        " и т.п. — считай дни от неё, а не выдумывай.\n"
    )


SUMMARY_SYSTEM = (
    "Ты ведёшь краткое саммари своей личной переписки в Telegram. Тебе"
    " дают текущее саммари и новые сообщения. Верни обновлённое саммари —"
    " связный текст на русском от первого лица: про себя пиши 'я', а"
    " собеседника называй по имени. По делу: ключевые темы,"
    " договорённости, факты о собеседнике, тон общения, незакрытые"
    " вопросы. Без вступлений и заголовков, не длиннее 1200 символов."
    " Только текст саммари."
)


def _self_name_pattern(user_name: str | None) -> Optional[re.Pattern]:
    """Regex matching the user's own name (full name and declined surname).

    Used to scrub the user's name out of context fed to the model and out
    of generated replies, so a clone model doesn't refer to itself by name
    in the third person.
    """
    name = (user_name or "").strip()
    if not name:
        return None
    alts = [re.escape(name)]
    for token in re.split(r"\s+", name):
        if len(token) >= 4:
            alts.append(re.escape(token) + r"[а-яёa-z]*")
    return re.compile(r"\b(?:" + "|".join(alts) + r")\b", re.IGNORECASE)


def _strip_self_name(text: str, pattern: Optional[re.Pattern]) -> str:
    """Remove the user's own name from text and tidy up the leftover gaps."""
    if not pattern or not text:
        return text
    cleaned = pattern.sub("", text)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"[ \t]+([,.!?…:;)])", r"\1", cleaned)
    cleaned = re.sub(r"([(])[ \t]+", r"\1", cleaned)
    cleaned = re.sub(r"(?m)^[ \t]+", "", cleaned)
    return cleaned.strip()


def build_system_prompt(
    user_name: str,
    style_profile: dict | None,
    sender_name: str,
    is_voice: bool = False,
    transcription: Optional[str] = None,
    rag_context: Optional[str] = None,
    summary: Optional[str] = None,
) -> str:
    style_str = json.dumps(style_profile or {}, ensure_ascii=False)
    name_re = _self_name_pattern(user_name)
    if name_re:
        rag_context = _strip_self_name(rag_context or "", name_re) or None
        summary = _strip_self_name(summary or "", name_re) or None
    rag_block = f"{rag_context.strip()}\n" if rag_context and rag_context.strip() else ""
    summary_block = (
        "Краткое саммари предыдущего общения с этим собеседником "
        f"(может охватывать недели переписки):\n{summary.strip()}\n"
        if summary and summary.strip()
        else ""
    )
    base = SYSTEM_TEMPLATE.format(
        user_name=user_name,
        style_profile=style_str,
        sender_name=sender_name or "неизвестно",
        rag_block=rag_block,
        summary_block=summary_block,
    )
    base = _current_date_block() + base
    if is_voice:
        voice_note = (
            "\nСобеседник отправил голосовое сообщение."
            f" Транскрипция: {(transcription or '').strip()}\n"
            "Отвечай как на обычное сообщение, не упоминай что это была"
            " голосовая запись."
        )
        base = base + voice_note
    return base


def build_chat_messages(
    user_name: str,
    style_profile: dict | None,
    sender_name: str,
    chat_history: list[dict] | None,
    incoming_text: str,
    is_voice: bool = False,
    transcription: Optional[str] = None,
    rag_context: Optional[str] = None,
    summary: Optional[str] = None,
) -> list[dict]:
    """Build a multi-turn messages array: system + alternating dialogue turns."""
    system = build_system_prompt(
        user_name,
        style_profile,
        sender_name,
        is_voice=is_voice,
        transcription=transcription,
        rag_context=rag_context,
        summary=summary,
    )
    messages: list[dict] = [{"role": "system", "content": system}]

    history = list(chat_history or [])
    incoming_clean = (incoming_text or "").strip()
    if history:
        last = history[-1]
        last_text = (last.get("text") or "").strip()
        if not last.get("is_mine") and last_text == incoming_clean:
            history = history[:-1]

    # Scrub the user's own name from prior turns: the model's earlier
    # replies are fed back as context, and if they named the user in the
    # third person the model keeps repeating the pattern.
    name_re = _self_name_pattern(user_name)
    for msg in history:
        text = (msg.get("text") or "").replace("\n", " ").strip()
        if not text:
            continue
        role = "assistant" if msg.get("is_mine") else "user"
        if role == "assistant" and name_re:
            text = _strip_self_name(text, name_re)
        if not text:
            continue
        messages.append({"role": role, "content": text})

    # Feed the model the raw incoming message as the final turn — no meta
    # wrapper, no formatting instructions. A chat-clone model replies best
    # to a clean conversation; wrapper text confuses it into answering the
    # wrapper instead of the message.
    final = incoming_clean or (incoming_text or "").strip()
    messages.append({"role": "user", "content": final})
    return _merge_consecutive_turns(messages)


def _merge_consecutive_turns(messages: list[dict]) -> list[dict]:
    """Collapse adjacent same-role turns so servers that require strict
    user/assistant alternation still accept the request."""
    merged: list[dict] = []
    for msg in messages:
        if merged and merged[-1]["role"] == msg["role"]:
            merged[-1]["content"] += "\n" + msg["content"]
        else:
            merged.append(dict(msg))
    return merged


_NAME_TEXT_FRAGMENT_RE = re.compile(
    r'^\s*["\']?([^"\':\n{}\[\]]{1,64})["\']?\s*:\s*["\'](.+)["\']\s*[,;]?\s*$',
    re.DOTALL,
)


def _coerce_variant(value, _depth: int = 0) -> str:
    if _depth > 4 or value is None:
        return ""
    if isinstance(value, dict):
        for key in ("text", "message", "content", "reply", "answer", "variant"):
            inner = value.get(key)
            if inner not in (None, ""):
                coerced = _coerce_variant(inner, _depth + 1)
                if coerced:
                    return coerced
        string_values = [v for v in value.values() if isinstance(v, str) and v.strip()]
        if len(string_values) == 1:
            return _coerce_variant(string_values[0], _depth + 1)
        return ""
    if isinstance(value, list):
        parts = [_coerce_variant(v, _depth + 1) for v in value]
        return " ".join(p for p in parts if p)

    text = str(value).strip().rstrip(",;").strip()
    if not text:
        return ""

    if (text.startswith("{") and text.endswith("}")) or (
        text.startswith("[") and text.endswith("]")
    ):
        try:
            return _coerce_variant(json.loads(text), _depth + 1)
        except json.JSONDecodeError:
            pass

    fragment = _NAME_TEXT_FRAGMENT_RE.match(text)
    if fragment:
        return _coerce_variant(fragment.group(2), _depth + 1)

    if len(text) >= 2 and text[0] == text[-1] and text[0] in ('"', "'"):
        try:
            unquoted = json.loads(text)
        except json.JSONDecodeError:
            unquoted = text[1:-1]
        if isinstance(unquoted, str) and unquoted.strip() and unquoted.strip() != text:
            return _coerce_variant(unquoted, _depth + 1)

    return text


def _looks_like_json_garbage(text: str) -> bool:
    if not text:
        return True
    stripped = text.strip()
    if not stripped:
        return True
    if '"variants"' in stripped:
        return True
    if stripped.startswith("{") or stripped.startswith("["):
        return True
    return False


_JSON_OBJECT_RE = re.compile(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", re.DOTALL)
_QUOTED_VALUE_RE = re.compile(r':\s*"((?:\\.|[^"\\])+)"', re.DOTALL)
_NUMBERED_START_RE = re.compile(
    r'(?m)^([ \t]*)(?:\d{1,2}[.\)])(?:\s|$)'
)


def _strip_speaker_prefix(text: str, user_name: str | None) -> str:
    if not text:
        return text
    cleaned = text.strip()
    if not cleaned:
        return cleaned
    while len(cleaned) >= 2 and cleaned[0] in '"\'`«' and cleaned[-1] in '"\'`»':
        inner = cleaned[1:-1].strip()
        if not inner:
            break
        cleaned = inner
    if not user_name:
        return cleaned
    name = user_name.strip()
    pattern = re.compile(
        rf'^\s*["\'«]?\s*{re.escape(name)}\s*["\'»]?\s*[:.,\-–—]\s+',
        re.IGNORECASE,
    )
    for _ in range(3):
        new = pattern.sub("", cleaned, count=1)
        if new == cleaned:
            break
        cleaned = new.strip()
    return cleaned


def _parse_numbered_list(raw: str) -> list[str]:
    matches = list(_NUMBERED_START_RE.finditer(raw))
    if not matches:
        return []
    base_indent = len(matches[0].group(1).expandtabs(4))
    top_level = [
        m for m in matches
        if len(m.group(1).expandtabs(4)) <= base_indent
    ]
    items: list[str] = []
    boundaries = [m.start() for m in top_level] + [len(raw)]
    for i, m in enumerate(top_level):
        chunk = raw[boundaries[i]:boundaries[i + 1]]
        body = chunk[m.end() - m.start():].strip()
        if not body:
            continue
        if "\n" in body:
            candidate = body
        else:
            candidate = _coerce_variant(body)
        if candidate and not _looks_like_json_garbage(candidate):
            items.append(candidate)
    return items


def _fallback_from_raw(raw: str, user_name: str | None) -> str:
    """Last-resort cleanup: use the raw model output as a single variant."""
    if not raw:
        return ""
    text = _coerce_variant(raw)
    if not text:
        text = raw.strip()
    text = _strip_speaker_prefix(text, user_name)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_single_reply(raw: str, user_name: str | None = None) -> str:
    """Clean a single chat reply from one model completion.

    The model is asked for a plain message, but defensively handle the case
    where it still wraps the answer in a numbered list, JSON, or quotes.
    Internal line breaks are preserved so a reply can later be split into
    several Telegram messages.
    """
    if not raw or not raw.strip():
        return ""
    items = _parse_numbered_list(raw)
    if len(items) >= 2:
        # model produced a numbered list despite instructions — take the first
        text = items[0]
    else:
        text = raw.strip()
        text = re.sub(r"^\s*\d{1,2}[.\)]\s+", "", text)
    text = _coerce_variant(text)
    text = _strip_speaker_prefix(text, user_name)
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln).strip()


def _extract_variants(raw: str, user_name: str | None = None) -> list[str]:
    if not raw:
        return []

    collected: list[str] = _parse_numbered_list(raw)

    if not collected:
        for candidate in _JSON_OBJECT_RE.findall(raw):
            try:
                data = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            variants = data.get("variants") if isinstance(data, dict) else None
            if isinstance(variants, list):
                cleaned = [_coerce_variant(v) for v in variants]
                cleaned = [c for c in cleaned if c and not _looks_like_json_garbage(c)]
                if cleaned:
                    collected = cleaned
                    break

    if not collected:
        seen: set[str] = set()
        for frag in _QUOTED_VALUE_RE.findall(raw):
            try:
                text = json.loads(f'"{frag}"')
            except json.JSONDecodeError:
                text = frag
            text = text.strip()
            if not text or _looks_like_json_garbage(text) or text in seen:
                continue
            seen.add(text)
            collected.append(text)
            if len(collected) >= 3:
                break

    if not collected:
        for line in raw.splitlines():
            ln = line.strip(" -•\t")
            if ln and not ln.startswith("{") and not ln.startswith("}") \
                    and not _looks_like_json_garbage(ln):
                collected.append(ln)

    if user_name and len(collected) == 1:
        single = collected[0]
        if single.lower().count(user_name.lower()) >= 2:
            parts = re.split(
                rf'["\'«]?\s*{re.escape(user_name)}\s*["\'»]?\s*[:.,\-–—]?',
                single,
                flags=re.IGNORECASE,
            )
            cleaned_parts: list[str] = []
            for p in parts:
                p = p.strip().strip('"\',.:;`').strip()
                if p:
                    cleaned_parts.append(p)
            if len(cleaned_parts) >= 2:
                collected = cleaned_parts

    final: list[str] = []
    seen_final: set[str] = set()
    for item in collected:
        cleaned = _strip_speaker_prefix(item, user_name)
        if not cleaned or _looks_like_json_garbage(cleaned) or cleaned in seen_final:
            continue
        seen_final.add(cleaned)
        final.append(cleaned)
        if len(final) >= 3:
            break
    return final


class LLMClient:
    """Common reply-building logic shared by all backend clients."""

    host: str
    model: str

    async def health(self) -> bool:  # pragma: no cover - overridden
        raise NotImplementedError

    async def list_models(self) -> list[str]:  # pragma: no cover - overridden
        raise NotImplementedError

    async def ensure_model(self, model: str | None = None) -> None:
        return None

    async def generate_raw(
        self,
        system: str,
        prompt: str,
        temperature: float = 0.8,
        num_predict: int | None = None,
        json_format: bool = False,
    ) -> str:  # pragma: no cover - overridden
        raise NotImplementedError

    async def generate_chat(
        self,
        messages: list[dict],
        temperature: float = 0.8,
        num_predict: int | None = None,
    ) -> str:  # pragma: no cover - overridden
        raise NotImplementedError

    async def generate_reply(
        self,
        incoming_text: str,
        sender_name: str,
        style_profile: Optional[dict] = None,
        chat_history: Optional[list[dict]] = None,
        user_name: Optional[str] = None,
        is_voice: bool = False,
        rag_context: Optional[str] = None,
        summary: Optional[str] = None,
        extra_system_context: Optional[str] = None,
    ) -> list[str]:
        effective_name = user_name or settings.user_name
        messages = build_chat_messages(
            effective_name,
            style_profile,
            sender_name,
            chat_history,
            incoming_text,
            is_voice=is_voice,
            transcription=incoming_text if is_voice else None,
            rag_context=rag_context,
            summary=summary,
        )
        await self._inject_calendar_context(
            messages, incoming_text, skip=bool(extra_system_context)
        )
        if (
            extra_system_context
            and messages
            and messages[0].get("role") == "system"
        ):
            messages[0]["content"] += "\n\n" + extra_system_context
        # Generate 3 variants by sampling the model independently at
        # different temperatures, instead of asking it for a numbered list
        # in a single call — a chat-clone model produces one reply, not a
        # formatted list.
        #
        # Sampled sequentially (not concurrently): a local single-slot server
        # may return 500 on parallel requests. A small output budget keeps
        # prompt + completion inside the model's context window.
        temperatures = (0.7, 0.85, 1.0)
        variants: list[str] = []
        seen: set[str] = set()
        errors: list[Exception] = []
        for temp in temperatures:
            try:
                raw = await self.generate_chat(
                    messages, temperature=temp, num_predict=REPLY_MAX_TOKENS
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
                log.warning("LLM sampling call failed: %s", exc)
                continue
            cand = _extract_single_reply(raw, effective_name)
            if cand and cand not in seen:
                seen.add(cand)
                variants.append(cand)
        if not variants:
            if errors:
                raise errors[0]
            log.warning("LLM produced no usable reply. model=%s", self.model)
        # Keep the bot from naming itself in the third person: prefer
        # variants free of the user's own name; if every variant names the
        # user, scrub the name out as a last resort.
        name_re = _self_name_pattern(effective_name)
        if name_re and variants:
            clean = [v for v in variants if not name_re.search(v)]
            if clean:
                variants = clean
            else:
                scrubbed: list[str] = []
                for v in variants:
                    s = _strip_self_name(v, name_re)
                    scrubbed.append(s if s else v)
                variants = scrubbed
        while len(variants) < 3:
            variants.append(variants[-1] if variants else "…")
        return variants[:3]

    @staticmethod
    async def _inject_calendar_context(
        messages: list[dict],
        incoming_text: str,
        skip: bool = False,
    ) -> None:
        """When the incoming message asks about availability or a meeting,
        append the real state of the calendar (today's and tomorrow's
        existing events plus the closest free slots) to the system prompt so
        the model answers from facts instead of guessing. Skipped when the
        caller already has an authoritative action memo to inject — iCloud
        reads can lag behind a just-performed create/cancel and produce a
        contradictory snapshot."""
        if skip:
            return
        try:
            from datetime import datetime, timedelta

            from .calendar_engine import (
                MONTHS_RU,
                calendar_engine,
            )
            from .meeting_detector import detect_meeting_intent

            if not calendar_engine.is_connected:
                return
            intent = await detect_meeting_intent(incoming_text)
            if not intent or not intent.has_intent:
                return

            today = datetime.now().replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            tomorrow = today + timedelta(days=1)
            day_after = today + timedelta(days=2)
            events = await calendar_engine.get_events(today, day_after)
            slots = await calendar_engine.get_free_slots()

            def _fmt_day(d: datetime) -> str:
                return f"{d.day} {MONTHS_RU[d.month - 1]}"

            def _fmt_event(e: dict) -> str:
                return (
                    f"- {e['start'].strftime('%H:%M')}"
                    f"–{e['end'].strftime('%H:%M')}: {e['title']}"
                )

            today_events = [e for e in events if e["start"].date() == today.date()]
            tomorrow_events = [
                e for e in events if e["start"].date() == tomorrow.date()
            ]

            lines = ["📅 Реальное состояние моего календаря:"]
            lines.append(f"Сегодня ({_fmt_day(today)}):")
            if today_events:
                lines.extend(_fmt_event(e) for e in today_events)
            else:
                lines.append("- встреч нет")
            lines.append(f"Завтра ({_fmt_day(tomorrow)}):")
            if tomorrow_events:
                lines.extend(_fmt_event(e) for e in tomorrow_events)
            else:
                lines.append("- встреч нет")
            if slots:
                lines.append("Ближайшие свободные слоты:")
                lines.extend(f"- {s['label']}" for s in slots)

            inject = (
                "\n\nСобеседник спрашивает про моё время или встречу."
                " Опирайся только на данные ниже — это реальный календарь."
                " Если он предлагает время, которое уже занято по списку"
                " событий — честно скажи, что в это время занят, и"
                " предложи альтернативу из свободных слотов. Не выдумывай"
                " свободные часы и не игнорируй существующие события.\n"
                + "\n".join(lines)
                + "\nОтвечай естественно, как в живом чате, без сухих списков."
            )
            if messages and messages[0].get("role") == "system":
                messages[0]["content"] += inject
        except Exception:  # noqa: BLE001
            log.exception("calendar context injection failed")

    async def summarize(
        self,
        previous_summary: str,
        dialogue_lines: list[str],
    ) -> str:
        """Roll a running summary forward with the given new dialogue lines.

        Returns the previous summary unchanged when there is nothing new or
        the model fails, so a transient error never wipes accumulated context.
        """
        lines = [ln.strip() for ln in dialogue_lines if ln and ln.strip()]
        if not lines:
            return previous_summary or ""
        prompt = (
            "Текущее саммари:\n"
            f"{(previous_summary or '').strip() or '(пока пусто)'}\n\n"
            "Новые сообщения переписки (по порядку):\n"
            + "\n".join(lines)
            + "\n\nВыдай обновлённое саммари одним связным текстом."
        )
        try:
            raw = await self.generate_raw(
                SUMMARY_SYSTEM, prompt, temperature=0.3, num_predict=512
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("summary generation failed: %s", exc)
            return previous_summary or ""
        cleaned = (raw or "").strip()
        if not cleaned:
            return previous_summary or ""
        cleaned = re.sub(
            r"^\s*(обновлённое\s+)?саммари\s*:\s*", "", cleaned, flags=re.IGNORECASE
        )
        return cleaned.strip()


class OpenAICompatibleClient(LLMClient):
    """Client for OpenAI-compatible servers (LM Studio, llama.cpp, vLLM, etc.)."""

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self.host = (base_url or settings.openai_base_url).rstrip("/")
        self.model = model or settings.openai_model
        self.api_key = api_key if api_key is not None else settings.openai_api_key

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{self.host}/models", headers=self._headers())
                return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def list_models(self) -> list[str]:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{self.host}/models", headers=self._headers())
                r.raise_for_status()
                data = r.json()
                return [m.get("id", "") for m in data.get("data", []) if m.get("id")]
        except httpx.HTTPError:
            return []

    async def _ensure_alive(self) -> None:
        if not await self.health():
            raise LLMUnavailableError(
                f"OpenAI-совместимый сервер недоступен на {self.host}. "
                "Проверь, что LM Studio / llama.cpp / vLLM запущен."
            )

    async def _post_chat(
        self,
        messages: list[dict],
        temperature: float,
        num_predict: int | None,
        json_format: bool = False,
    ) -> str:
        await self._ensure_alive()
        if num_predict is None:
            num_predict = settings.llm_max_tokens
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": num_predict,
            "stream": False,
        }
        if json_format:
            payload["response_format"] = {"type": "json_object"}
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(
                f"{self.host}/chat/completions",
                json=payload,
                headers=self._headers(),
            )
            r.raise_for_status()
            data = r.json()
            choices = data.get("choices") or []
            if not choices:
                return ""
            message = choices[0].get("message") or {}
            return message.get("content", "") or ""

    async def generate_raw(
        self,
        system: str,
        prompt: str,
        temperature: float = 0.8,
        num_predict: int | None = None,
        json_format: bool = False,
    ) -> str:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]
        return await self._post_chat(messages, temperature, num_predict, json_format)

    async def generate_chat(
        self,
        messages: list[dict],
        temperature: float = 0.8,
        num_predict: int | None = None,
    ) -> str:
        return await self._post_chat(messages, temperature, num_predict)


_default_client: Optional[LLMClient] = None


def get_client() -> LLMClient:
    global _default_client
    if _default_client is None:
        _default_client = OpenAICompatibleClient()
    return _default_client


def reset_client() -> None:
    """Drop the cached client (useful when settings change at runtime/tests)."""
    global _default_client
    _default_client = None
