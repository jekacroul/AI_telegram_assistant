"""Local Whisper speech-to-text engine.

VRAM budgets (rough estimates, FP16):
- tiny:     ~1 GB VRAM
- base:     ~1 GB VRAM
- small:    ~1.5 GB VRAM (acceptable quality)
- medium:   ~3 GB VRAM (good quality)
- large-v3: ~6 GB VRAM (best quality, especially for Russian)

On a 12 GB GPU running saiga 12B Q8 (~13 GB) you can't fit large-v3
simultaneously — either pick medium or enable lazy_load to load and
unload Whisper around each request.
"""
from __future__ import annotations

import asyncio
import gc
import logging
import os
import threading
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)


DEFAULT_MODEL = "large-v3"
DEFAULT_LANGUAGE = "ru"
TRANSCRIBE_TIMEOUT_SECONDS = 60.0
LOW_CONFIDENCE_THRESHOLD = 0.5


@dataclass
class TranscribeResult:
    text: str = ""
    language: str = ""
    duration: float = 0.0
    confidence: float = 0.0
    low_confidence: bool = False
    error: Optional[str] = None


class WhisperEngine:
    """Lazy/cached loader for the whisper model.

    Thread-safe: model is loaded once and reused. With ``lazy_load=True``
    we load on demand and free VRAM right after transcription so the LLM
    can use the GPU memory.
    """

    def __init__(self) -> None:
        self._model = None
        self._model_name: str = ""
        self._device: str = "cpu"
        self._lock = threading.Lock()
        self._ffmpeg_ok: Optional[bool] = None
        self._last_error: str = ""

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def device(self) -> str:
        return self._device

    @property
    def last_error(self) -> str:
        return self._last_error

    def ffmpeg_available(self) -> bool:
        if self._ffmpeg_ok is not None:
            return self._ffmpeg_ok
        from shutil import which

        self._ffmpeg_ok = which("ffmpeg") is not None
        if not self._ffmpeg_ok:
            log.warning("ffmpeg not found in PATH; voice transcription will fail")
        return self._ffmpeg_ok

    def detect_device(self) -> str:
        try:
            import torch  # type: ignore

            if torch.cuda.is_available():
                return "cuda"
        except Exception:  # noqa: BLE001
            pass
        return "cpu"

    def vram_used_mb(self) -> Optional[int]:
        if self._device != "cuda":
            return None
        try:
            import torch  # type: ignore

            return int(torch.cuda.memory_allocated() / (1024 * 1024))
        except Exception:  # noqa: BLE001
            return None

    def load(self, model_name: str = DEFAULT_MODEL) -> bool:
        with self._lock:
            if self._model is not None and self._model_name == model_name:
                return True
            try:
                import whisper  # type: ignore
            except ImportError as e:
                self._last_error = (
                    f"openai-whisper is not installed: {e}. "
                    "pip install openai-whisper"
                )
                log.error(self._last_error)
                return False

            device = self.detect_device()
            try:
                model = whisper.load_model(model_name, device=device)
            except Exception as e:  # noqa: BLE001
                if device == "cuda":
                    log.warning(
                        "CUDA load failed for whisper %s (%s); falling back to CPU",
                        model_name,
                        e,
                    )
                    try:
                        model = whisper.load_model(model_name, device="cpu")
                        device = "cpu"
                    except Exception as e2:  # noqa: BLE001
                        self._last_error = f"whisper load failed on CPU: {e2}"
                        log.exception("whisper CPU load failed")
                        return False
                else:
                    self._last_error = f"whisper load failed: {e}"
                    log.exception("whisper load failed")
                    return False

            self._model = model
            self._model_name = model_name
            self._device = device
            self._last_error = ""
            log.info("Loaded whisper model %s on %s", model_name, device)
            return True

    def unload(self) -> None:
        with self._lock:
            if self._model is None:
                return
            self._model = None
            self._model_name = ""
            gc.collect()
            try:
                import torch  # type: ignore

                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:  # noqa: BLE001
                pass
            log.info("Whisper model unloaded; VRAM freed")

    def _transcribe_sync(
        self, file_path: str, language: str, model_name: str
    ) -> TranscribeResult:
        if not os.path.exists(file_path):
            return TranscribeResult(text="", error="file not found")
        if not self.ffmpeg_available():
            return TranscribeResult(
                text="", error="ffmpeg not installed (add to PATH)"
            )
        if not self.load(model_name):
            return TranscribeResult(
                text="", error=self._last_error or "model load failed"
            )

        try:
            kwargs: dict = {"task": "transcribe"}
            if language and language != "auto":
                kwargs["language"] = language
            with self._lock:
                model = self._model
                if model is None:
                    return TranscribeResult(text="", error="model unloaded")
                result = model.transcribe(file_path, **kwargs)
        except Exception as e:  # noqa: BLE001
            log.exception("whisper transcription failed")
            return TranscribeResult(text="", error=f"transcription failed: {e}")

        segments = result.get("segments") or []
        if segments:
            logprobs = [
                s.get("avg_logprob", 0.0)
                for s in segments
                if s.get("avg_logprob") is not None
            ]
            avg_logprob = sum(logprobs) / len(logprobs) if logprobs else 0.0
            import math

            confidence = float(math.exp(avg_logprob)) if logprobs else 0.0
        else:
            confidence = 0.0

        duration = float(result.get("duration") or 0.0)
        if not duration and segments:
            duration = float(segments[-1].get("end") or 0.0)

        text = (result.get("text") or "").strip()
        return TranscribeResult(
            text=text,
            language=result.get("language") or language or "",
            duration=duration,
            confidence=confidence,
            low_confidence=confidence < LOW_CONFIDENCE_THRESHOLD,
        )

    async def transcribe_voice(
        self,
        file_path: str,
        language: str = DEFAULT_LANGUAGE,
        model_name: str = DEFAULT_MODEL,
        lazy_load: bool = False,
    ) -> TranscribeResult:
        """Transcribe an audio file. Wraps the blocking whisper call in a
        thread and applies a timeout so a stuck transcription cannot block
        the event loop forever."""
        loop = asyncio.get_running_loop()
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(
                    None, self._transcribe_sync, file_path, language, model_name
                ),
                timeout=TRANSCRIBE_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            log.warning("whisper transcription timed out for %s", file_path)
            return TranscribeResult(text="", error="transcription timed out")
        if lazy_load:
            try:
                self.unload()
            except Exception:  # noqa: BLE001
                log.exception("failed to unload whisper after lazy transcription")
        return result


whisper_engine = WhisperEngine()


def convert_to_wav(src_path: str, dst_path: str) -> bool:
    """Convert an arbitrary audio file (ogg/opus from Telegram) to wav via
    pydub. Returns True on success."""
    try:
        from pydub import AudioSegment  # type: ignore
    except ImportError:
        log.error("pydub not installed; cannot convert audio")
        return False
    try:
        audio = AudioSegment.from_file(src_path)
        audio.export(dst_path, format="wav")
        return True
    except Exception:  # noqa: BLE001
        log.exception("audio conversion failed")
        return False
