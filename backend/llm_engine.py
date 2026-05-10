from __future__ import annotations

import json
import re
from typing import Iterable, Optional

import httpx

from .config import settings


class OllamaUnavailableError(RuntimeError):
    pass


SYSTEM_TEMPLATE = (
    "Ты — {user_name}. Отвечай точно в его стиле.\n"
    "Стиль: {style_profile}\n"
    "Собеседник: {sender_name}\n"
    "История чата: {chat_history}\n"
    "Генерируй ровно 3 варианта ответа. Отвечай только JSON:\n"
    '{{"variants": ["...", "...", "..."]}}'
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


def _extract_variants(raw: str) -> list[str]:
    if not raw:
        return []
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        candidate = match.group(0)
        try:
            data = json.loads(candidate)
            variants = data.get("variants")
            if isinstance(variants, list):
                cleaned = [str(v).strip() for v in variants if str(v).strip()]
                if cleaned:
                    return cleaned[:3]
        except json.JSONDecodeError:
            pass

    lines = [ln.strip(" -•\t") for ln in raw.splitlines() if ln.strip()]
    cleaned = [ln for ln in lines if ln and not ln.startswith("{") and not ln.startswith("}")]
    return cleaned[:3] if cleaned else [raw.strip()]


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
        system = build_system_prompt(
            user_name or settings.user_name,
            style_profile,
            sender_name,
            chat_history,
        )
        prompt = f"Сообщение собеседника: {incoming_text}\nДай ровно 3 варианта ответа."
        raw = await self.generate_raw(system, prompt)
        variants = _extract_variants(raw)
        while len(variants) < 3:
            variants.append(variants[-1] if variants else "ок")
        return variants[:3]


_default_client: Optional[OllamaClient] = None


def get_client() -> OllamaClient:
    global _default_client
    if _default_client is None:
        _default_client = OllamaClient()
    return _default_client
