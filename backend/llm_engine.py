"""Ollama integration: status, model management and reply generation."""
from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from .config import get_settings

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = httpx.Timeout(120.0, connect=10.0)


class OllamaUnavailable(RuntimeError):
    pass


class LLMEngine:
    def __init__(self, host: str | None = None, model: str | None = None) -> None:
        settings = get_settings()
        self.host = host or settings.ollama_host
        self.model = model or settings.ollama_model
        self._active_adapter_path: str | None = None

    def set_model(self, model: str) -> None:
        self.model = model

    def set_active_adapter(self, adapter_path: str | None) -> None:
        """Track which LoRA adapter the trainer most recently activated.

        Note: Ollama does not natively load arbitrary HF LoRA adapters; the
        trainer is responsible for converting / creating an Ollama Modelfile.
        Here we just store the metadata for the UI status endpoint.
        """
        self._active_adapter_path = adapter_path

    @property
    def active_adapter(self) -> str | None:
        return self._active_adapter_path

    async def status(self) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self.host}/api/tags")
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            return {
                "running": False,
                "host": self.host,
                "model": self.model,
                "error": str(exc),
                "models": [],
                "active_adapter": self._active_adapter_path,
            }
        models = [m.get("name") for m in data.get("models", [])]
        return {
            "running": True,
            "host": self.host,
            "model": self.model,
            "models": models,
            "model_loaded": self.model in models,
            "active_adapter": self._active_adapter_path,
        }

    async def ensure_model(self) -> None:
        """Pull the configured Ollama model if it is not already present."""
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            try:
                resp = await client.get(f"{self.host}/api/tags")
                resp.raise_for_status()
            except Exception as exc:
                raise OllamaUnavailable(f"Ollama not reachable at {self.host}: {exc}") from exc
            tags = {m.get("name") for m in resp.json().get("models", [])}
            if self.model in tags:
                return
            log.info("Pulling Ollama model %s", self.model)
            async with client.stream(
                "POST",
                f"{self.host}/api/pull",
                json={"name": self.model, "stream": True},
                timeout=None,
            ) as stream:
                async for line in stream.aiter_lines():
                    if not line:
                        continue
                    try:
                        evt = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if evt.get("status"):
                        log.info("ollama pull: %s", evt["status"])

    def _build_system_prompt(
        self,
        style_profile: dict[str, Any] | None,
        persona: str | None,
    ) -> str:
        lines = [
            "You are an AI that drafts replies on behalf of the user for their Telegram messages.",
            "Reply in the same language as the incoming message (default: Russian).",
            "Mimic the user's writing style as described below — tone, length, punctuation, emoji use.",
            "Never break character. Do not greet unnecessarily. Be natural, like a real chat reply.",
        ]
        if persona:
            lines.append(f"Persona notes: {persona.strip()}")
        if style_profile:
            lines.append("Style profile (JSON):")
            lines.append(json.dumps(style_profile, ensure_ascii=False))
        lines.append(
            "Return ONLY a JSON object of the form "
            '{"variants": ["...", "...", "..."]} with exactly 3 distinct reply candidates. '
            "Do not include any text outside the JSON."
        )
        return "\n".join(lines)

    def _format_history(self, conversation_history: list[dict[str, str]] | None) -> str:
        if not conversation_history:
            return ""
        chunks = []
        for entry in conversation_history[-10:]:
            who = "Me" if entry.get("is_mine") else entry.get("sender_name", "Them")
            chunks.append(f"{who}: {entry.get('text', '').strip()}")
        return "\n".join(chunks)

    async def generate_reply(
        self,
        incoming_message: str,
        style_profile: dict[str, Any] | None = None,
        conversation_history: list[dict[str, str]] | None = None,
        persona: str | None = None,
        n_variants: int = 3,
        temperature: float = 0.85,
    ) -> dict[str, Any]:
        """Generate ``n_variants`` reply candidates via Ollama chat API."""
        system_prompt = self._build_system_prompt(style_profile, persona)
        history_block = self._format_history(conversation_history)
        user_prompt_parts = []
        if history_block:
            user_prompt_parts.append(f"Recent conversation:\n{history_block}")
        user_prompt_parts.append(f"Incoming message to reply to:\n{incoming_message.strip()}")
        user_prompt_parts.append(
            f"Produce {n_variants} different reply variants in JSON as instructed."
        )
        user_prompt = "\n\n".join(user_prompt_parts)

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "format": "json",
            "options": {"temperature": temperature, "top_p": 0.95},
        }
        try:
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                resp = await client.post(f"{self.host}/api/chat", json=payload)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            raise OllamaUnavailable(f"Ollama request failed: {exc}") from exc

        content = (data.get("message") or {}).get("content", "")
        variants = self._parse_variants(content, n_variants)
        return {"variants": variants, "raw": content}

    @staticmethod
    def _parse_variants(content: str, n_variants: int) -> list[str]:
        content = content.strip()
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict) and isinstance(parsed.get("variants"), list):
                variants = [str(v).strip() for v in parsed["variants"] if str(v).strip()]
                if variants:
                    return variants[:n_variants]
        except json.JSONDecodeError:
            pass
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(0))
                if isinstance(parsed, dict) and isinstance(parsed.get("variants"), list):
                    variants = [str(v).strip() for v in parsed["variants"] if str(v).strip()]
                    if variants:
                        return variants[:n_variants]
            except json.JSONDecodeError:
                pass
        # Fallback: split lines, treat each non-empty line as a variant.
        lines = [ln.strip("-•* \t") for ln in content.splitlines() if ln.strip()]
        return lines[:n_variants] if lines else [content]

    async def test_prompt(self, prompt: str) -> str:
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.post(f"{self.host}/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()
        return (data.get("message") or {}).get("content", "")


llm_engine = LLMEngine()
