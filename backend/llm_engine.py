from __future__ import annotations

import json
import logging
import random
import re
from typing import Iterable, Optional

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


SYSTEM_TEMPLATE = (
    "Ты — {user_name}. Ты пишешь сообщение в Telegram собеседнику {sender_name}"
    " от первого лица, как живой человек. Ни в коем случае не пиши своё имя"
    " ('{user_name}'), не ставь его перед текстом, не оборачивай ответ в JSON,"
    " не используй кавычки, двоеточия-разделители, поля sender/text. Просто"
    " сам текст сообщения, как ты бы написал его в чате.\n"
    "Стиль речи: {style_profile}\n"
    "{rag_block}"
    "История чата:\n{chat_history}\n"
    "Дай ровно 3 разных варианта ответа. Формат — нумерованный список,"
    " каждый вариант с новой строки:\n"
    "1. <текст первого варианта>\n"
    "2. <текст второго варианта>\n"
    "3. <текст третьего варианта>\n"
    "Никаких других пояснений, заголовков или JSON."
)


def _format_history(chat_history: Iterable[dict] | None) -> str:
    if not chat_history:
        return "(нет)"
    lines = []
    for msg in chat_history:
        who = msg.get("sender_name") or ("Я" if msg.get("is_mine") else "Собеседник")
        text = msg.get("text", "").replace("\n", " ").strip()
        if text:
            lines.append(f"{who}: {text}")
    return "\n".join(lines) if lines else "(нет)"


def build_system_prompt(
    user_name: str,
    style_profile: dict | None,
    sender_name: str,
    chat_history: Iterable[dict] | None,
    is_voice: bool = False,
    transcription: Optional[str] = None,
    rag_context: Optional[str] = None,
) -> str:
    style_str = json.dumps(style_profile or {}, ensure_ascii=False)
    rag_block = f"{rag_context.strip()}\n" if rag_context and rag_context.strip() else ""
    base = SYSTEM_TEMPLATE.format(
        user_name=user_name,
        style_profile=style_str,
        sender_name=sender_name or "неизвестно",
        chat_history=_format_history(chat_history),
        rag_block=rag_block,
    )
    if is_voice:
        voice_note = (
            "\nСобеседник отправил голосовое сообщение."
            f" Транскрипция: {(transcription or '').strip()}\n"
            "Отвечай как на обычное сообщение, не упоминай что это была"
            " голосовая запись."
        )
        base = base + voice_note
    return base


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

    async def generate_reply(
        self,
        incoming_text: str,
        sender_name: str,
        style_profile: Optional[dict] = None,
        chat_history: Optional[list[dict]] = None,
        user_name: Optional[str] = None,
        is_voice: bool = False,
        rag_context: Optional[str] = None,
    ) -> list[str]:
        effective_name = user_name or settings.user_name
        system = build_system_prompt(
            effective_name,
            style_profile,
            sender_name,
            chat_history,
            is_voice=is_voice,
            transcription=incoming_text if is_voice else None,
            rag_context=rag_context,
        )
        prompt = (
            f"Сообщение собеседника ({sender_name or 'неизвестно'}): {incoming_text}\n"
            "Ответь как продолжение чата от первого лица."
            " Дай ровно 3 разных варианта ответа в виде нумерованного списка"
            " (1., 2., 3.), без своего имени и без JSON."
        )
        raw = await self.generate_raw(system, prompt)
        variants = _extract_variants(raw, user_name=effective_name)
        if not variants:
            fallback = _fallback_from_raw(raw, effective_name)
            log.warning(
                "LLM response did not match expected format; using raw output as fallback. "
                "model=%s raw=%r",
                self.model,
                raw,
            )
            if fallback:
                variants = [fallback]
        while len(variants) < 3:
            variants.append(variants[-1] if variants else "…")
        return variants[:3]


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

    async def generate_raw(
        self,
        system: str,
        prompt: str,
        temperature: float = 0.8,
        num_predict: int | None = None,
        json_format: bool = False,
    ) -> str:
        await self._ensure_alive()
        if num_predict is None:
            num_predict = settings.llm_max_tokens
        payload: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
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
