from __future__ import annotations

import json
import re
from typing import Iterable, Optional

import httpx

from .config import settings


class OllamaUnavailableError(RuntimeError):
    pass


SYSTEM_TEMPLATE = (
    "Ты — {user_name}. Ты пишешь сообщение в Telegram собеседнику {sender_name}"
    " от первого лица, как живой человек. Ни в коем случае не пиши своё имя"
    " ('{user_name}'), не ставь его перед текстом, не оборачивай ответ в JSON,"
    " не используй кавычки, двоеточия-разделители, поля sender/text. Просто"
    " сам текст сообщения, как ты бы написал его в чате.\n"
    "Стиль речи: {style_profile}\n"
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
) -> str:
    style_str = json.dumps(style_profile or {}, ensure_ascii=False)
    return SYSTEM_TEMPLATE.format(
        user_name=user_name,
        style_profile=style_str,
        sender_name=sender_name or "неизвестно",
        chat_history=_format_history(chat_history),
    )


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
    if stripped.count("{") + stripped.count("}") >= 3:
        return True
    if stripped.startswith("{") or stripped.startswith("["):
        return True
    return False


_JSON_OBJECT_RE = re.compile(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", re.DOTALL)
_QUOTED_VALUE_RE = re.compile(r':\s*"((?:\\.|[^"\\])+)"', re.DOTALL)
_NUMBERED_LINE_RE = re.compile(r'^\s*(?:[\-•*]|\(?\d{1,2}[.\)\]:])\s+(.+?)\s*$')


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
    items: list[str] = []
    for line in raw.splitlines():
        m = _NUMBERED_LINE_RE.match(line)
        if not m:
            continue
        candidate = _coerce_variant(m.group(1))
        if candidate and not _looks_like_json_garbage(candidate):
            items.append(candidate)
    return items


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


class OllamaClient:
    def __init__(self, host: str | None = None, model: str | None = None) -> None:
        self.host = (host or settings.ollama_host).rstrip("/")
        self.model = model or settings.ollama_model

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{self.host}/api/tags")
                return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def list_models(self) -> list[str]:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{self.host}/api/tags")
                r.raise_for_status()
                data = r.json()
                return [m.get("name", "") for m in data.get("models", [])]
        except httpx.HTTPError:
            return []

    async def ensure_model(self, model: str | None = None) -> None:
        target = model or self.model
        models = await self.list_models()
        if any(m == target or m.startswith(f"{target}:") for m in models):
            return
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("POST", f"{self.host}/api/pull", json={"name": target}) as r:
                async for _ in r.aiter_lines():
                    pass

    async def _ensure_alive(self) -> None:
        if not await self.health():
            raise OllamaUnavailableError(
                f"Ollama не запущен на {self.host}. Запусти `ollama serve`."
            )

    async def generate_raw(
        self,
        system: str,
        prompt: str,
        temperature: float = 0.8,
        num_predict: int = 512,
        json_format: bool = False,
    ) -> str:
        await self._ensure_alive()
        payload = {
            "model": self.model,
            "system": system,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": num_predict,
            },
        }
        if json_format:
            payload["format"] = "json"
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(f"{self.host}/api/generate", json=payload)
            r.raise_for_status()
            data = r.json()
            return data.get("response", "")

    async def generate_reply(
        self,
        incoming_text: str,
        sender_name: str,
        style_profile: Optional[dict] = None,
        chat_history: Optional[list[dict]] = None,
        user_name: Optional[str] = None,
    ) -> list[str]:
        effective_name = user_name or settings.user_name
        system = build_system_prompt(
            effective_name,
            style_profile,
            sender_name,
            chat_history,
        )
        prompt = (
            f"Сообщение собеседника ({sender_name or 'неизвестно'}): {incoming_text}\n"
            "Ответь как продолжение чата от первого лица."
            " Дай ровно 3 разных варианта ответа в виде нумерованного списка"
            " (1., 2., 3.), без своего имени и без JSON."
        )
        raw = await self.generate_raw(system, prompt)
        variants = _extract_variants(raw, user_name=effective_name)
        while len(variants) < 3:
            variants.append(variants[-1] if variants else "ок")
        return variants[:3]


_default_client: Optional[OllamaClient] = None


def get_client() -> OllamaClient:
    global _default_client
    if _default_client is None:
        _default_client = OllamaClient()
    return _default_client
