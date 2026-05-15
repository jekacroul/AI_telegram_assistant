"""Retrieval-Augmented Generation: vector memory over the whole chat history.

Instead of only feeding the LLM the last few messages, the bot embeds every
message into a local ChromaDB collection and retrieves the semantically
closest ones at reply time. The embedding model and the vector store are
fully local — nothing leaves the machine.

Failure policy: every public coroutine degrades gracefully. If ChromaDB or
the embedding model is unavailable RAG simply disables itself for the
session and the bot keeps working without it.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select

from .config import settings

log = logging.getLogger(__name__)


EMBED_MODEL_NAME = "paraphrase-multilingual-mpnet-base-v2"
COLLECTION_NAME = "messages"
BATCH_SIZE = 64
SEARCH_TIMEOUT_SECONDS = 2.0


@dataclass
class RelevantMessage:
    text: str
    sender_name: str
    timestamp: str
    similarity_score: float
    chat_name: str
    chat_id: int = 0
    message_id: int = 0

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "sender_name": self.sender_name,
            "timestamp": self.timestamp,
            "similarity_score": round(self.similarity_score, 4),
            "chat_name": self.chat_name,
            "chat_id": self.chat_id,
            "message_id": self.message_id,
        }


@dataclass
class IndexResult:
    total: int = 0
    indexed: int = 0
    skipped: int = 0
    duration_seconds: float = 0.0


@dataclass
class RAGStats:
    total_indexed: int = 0
    collection_size_mb: float = 0.0
    last_indexed_at: Optional[str] = None


@dataclass
class _Progress:
    running: bool = False
    indexed: int = 0
    total: int = 0
    eta_seconds: float = 0.0

    @property
    def percent(self) -> float:
        if self.total <= 0:
            return 0.0
        return min(100.0, round(self.indexed / self.total * 100, 1))

    def to_dict(self) -> dict:
        return {
            "running": self.running,
            "indexed": self.indexed,
            "total": self.total,
            "percent": self.percent,
            "eta_seconds": round(self.eta_seconds, 1),
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _truncate(text: str, limit: int = 100) -> str:
    text = (text or "").replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _relative_time(iso_ts: str) -> str:
    """Render an ISO timestamp as a coarse Russian relative phrase."""
    if not iso_ts:
        return "недавно"
    try:
        raw = iso_ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return "недавно"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - dt
    secs = delta.total_seconds()
    if secs < 0:
        return "только что"
    minutes = secs / 60
    hours = secs / 3600
    days = secs / 86400
    if minutes < 1:
        return "только что"
    if minutes < 60:
        return f"{int(minutes)} мин назад"
    if hours < 24:
        return f"{int(hours)} ч назад"
    if days < 2:
        return "вчера"
    if days < 7:
        return f"{int(days)} дня назад" if int(days) < 5 else f"{int(days)} дней назад"
    if days < 31:
        weeks = int(days // 7)
        return f"{weeks} недели назад" if weeks < 5 else f"{weeks} недель назад"
    months = int(days // 30)
    return f"{months} месяц назад" if months == 1 else f"{months} месяцев назад"


def build_rag_context(relevant_messages: list[RelevantMessage]) -> str:
    """Format retrieved messages into a block for the LLM system prompt."""
    if not relevant_messages:
        return ""
    lines = ["Релевантный контекст из истории переписки:"]
    for m in relevant_messages:
        when = _relative_time(m.timestamp)
        who = m.sender_name or m.chat_name or "собеседник"
        lines.append(f'- [{when}, {who}]: "{_truncate(m.text)}"')
    return "\n".join(lines)


class RAGEngine:
    """Lazy singleton around the embedding model and ChromaDB collection."""

    def __init__(self) -> None:
        self._model = None
        self._client = None
        self._collection = None
        self._device = "cpu"
        self._available = False
        self._lock = asyncio.Lock()
        self._last_error = ""
        self._last_indexed_at: Optional[str] = None
        self._progress = _Progress()

    @property
    def available(self) -> bool:
        return self._available

    @property
    def device(self) -> str:
        return self._device

    @property
    def last_error(self) -> str:
        return self._last_error

    @property
    def progress(self) -> dict:
        return self._progress.to_dict()

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------
    async def initialize(self) -> bool:
        """Load the embedding model and open the ChromaDB collection.

        Returns True when RAG is usable. Safe to call repeatedly.
        """
        async with self._lock:
            if self._available:
                return True
            try:
                await asyncio.to_thread(self._load_sync)
                self._available = True
                self._last_error = ""
                log.info("RAG initialized (device=%s)", self._device)
                return True
            except Exception as e:  # noqa: BLE001
                self._available = False
                self._last_error = str(e)
                log.exception("RAG initialization failed; RAG disabled")
                return False

    def _load_sync(self) -> None:
        import chromadb
        from sentence_transformers import SentenceTransformer

        try:
            import torch

            self._device = "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:  # noqa: BLE001
            self._device = "cpu"

        self._model = SentenceTransformer(
            EMBED_MODEL_NAME,
            device=self._device,
            cache_folder=str(settings.sentence_transformers_dir),
        )
        self._client = chromadb.PersistentClient(path=str(settings.chroma_db_dir))
        self._collection = self._client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )

    def _recreate_collection(self) -> None:
        try:
            self._client.delete_collection(COLLECTION_NAME)
        except Exception:  # noqa: BLE001 - collection may not exist
            pass
        self._collection = self._client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )

    # ------------------------------------------------------------------
    # Embedding helpers
    # ------------------------------------------------------------------
    def _embed_sync(self, texts: list[str], show_progress: bool = False) -> list[list[float]]:
        vectors = self._model.encode(
            texts,
            batch_size=BATCH_SIZE,
            show_progress_bar=show_progress,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        return [v.tolist() for v in vectors]

    @staticmethod
    def _clean_metadata(metadata: dict) -> dict:
        """ChromaDB only accepts str/int/float/bool scalar metadata values."""
        cleaned: dict = {}
        for key, value in (metadata or {}).items():
            if value is None:
                continue
            if isinstance(value, bool) or isinstance(value, (int, float, str)):
                cleaned[key] = value
            else:
                cleaned[key] = str(value)
        return cleaned

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------
    async def index_message(
        self, message_id: int, text: str, metadata: dict
    ) -> None:
        """Add (or update) a single message in the vector store.

        ``message_id`` is the SQLite primary key. Fire-and-forget safe:
        any failure is logged and swallowed.
        """
        if not text or not text.strip():
            return
        if not self._available and not await self.initialize():
            return
        try:
            doc_id = str(message_id)
            meta = self._clean_metadata({**metadata, "message_id": message_id})
            embedding = (await asyncio.to_thread(self._embed_sync, [text]))[0]
            await asyncio.to_thread(
                self._collection.upsert,
                ids=[doc_id],
                embeddings=[embedding],
                documents=[text],
                metadatas=[meta],
            )
            self._last_indexed_at = _now_iso()
            await self._mark_indexed([message_id])
        except Exception:  # noqa: BLE001
            log.exception("RAG index_message failed for id=%s", message_id)

    async def index_all_messages(self, session) -> IndexResult:
        """Bulk-index every not-yet-indexed message from SQLite."""
        from .database import Message, RagIndexLog

        result = IndexResult()
        started = time.monotonic()
        if not self._available and not await self.initialize():
            return result

        rows = (
            await session.execute(
                select(
                    Message.id,
                    Message.text,
                    Message.chat_id,
                    Message.chat_name,
                    Message.sender_name,
                    Message.is_mine,
                    Message.timestamp,
                    Message.message_id,
                ).where(Message.deleted == False)  # noqa: E712
            )
        ).all()

        pending = [r for r in rows if (r.text or "").strip()]
        result.total = len(rows)
        result.skipped = len(rows) - len(pending)

        # Drop rows already present in the collection.
        if pending:
            existing = await self._existing_ids([str(r.id) for r in pending])
            new_rows = [r for r in pending if str(r.id) not in existing]
            result.skipped += len(pending) - len(new_rows)
            pending = new_rows

        self._progress = _Progress(running=True, total=len(pending))
        indexed_ids: list[int] = []
        try:
            for start in range(0, len(pending), BATCH_SIZE):
                batch = pending[start : start + BATCH_SIZE]
                texts = [r.text for r in batch]
                embeddings = await asyncio.to_thread(
                    self._embed_sync, texts, True
                )
                metadatas = [
                    self._clean_metadata(
                        {
                            "chat_id": r.chat_id,
                            "chat_name": r.chat_name,
                            "sender_name": r.sender_name,
                            "is_mine": bool(r.is_mine),
                            "timestamp": _iso_or_empty(r.timestamp),
                            "message_id": r.id,
                        }
                    )
                    for r in batch
                ]
                await asyncio.to_thread(
                    self._collection.upsert,
                    ids=[str(r.id) for r in batch],
                    embeddings=embeddings,
                    documents=texts,
                    metadatas=metadatas,
                )
                indexed_ids.extend(r.id for r in batch)
                result.indexed += len(batch)
                self._progress.indexed = result.indexed
                elapsed = time.monotonic() - started
                rate = result.indexed / elapsed if elapsed > 0 else 0
                remaining = len(pending) - result.indexed
                self._progress.eta_seconds = (
                    remaining / rate if rate > 0 else 0.0
                )
                log.info(
                    "RAG: индексирую %s/%s", result.indexed, len(pending)
                )
        finally:
            self._progress.running = False

        if indexed_ids:
            await self._mark_indexed(indexed_ids)

        result.duration_seconds = round(time.monotonic() - started, 2)
        self._last_indexed_at = _now_iso()
        session.add(
            RagIndexLog(
                total_messages=result.total,
                indexed_count=result.indexed,
                duration_seconds=result.duration_seconds,
            )
        )
        await session.commit()
        return result

    async def _existing_ids(self, ids: list[str]) -> set[str]:
        try:
            found: set[str] = set()
            for start in range(0, len(ids), 500):
                chunk = ids[start : start + 500]
                got = await asyncio.to_thread(self._collection.get, ids=chunk)
                found.update(got.get("ids") or [])
            return found
        except Exception:  # noqa: BLE001
            log.exception("RAG _existing_ids failed")
            return set()

    @staticmethod
    async def _mark_indexed(message_ids: list[int]) -> None:
        from .database import Message, SessionLocal

        if not message_ids:
            return
        try:
            async with SessionLocal() as session:
                rows = (
                    await session.execute(
                        select(Message).where(Message.id.in_(message_ids))
                    )
                ).scalars().all()
                for row in rows:
                    row.rag_indexed = True
                await session.commit()
        except Exception:  # noqa: BLE001
            log.exception("RAG _mark_indexed failed")

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------
    async def search_relevant(
        self,
        query: str,
        chat_id: int,
        limit: int = 5,
        min_similarity: float = 0.6,
    ) -> list[RelevantMessage]:
        """Find semantically similar messages, favouring the current chat."""
        if not query or not query.strip():
            return []
        if not self._available and not await self.initialize():
            return []
        try:
            return await asyncio.wait_for(
                self._search_relevant_inner(query, chat_id, limit, min_similarity),
                timeout=SEARCH_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            log.warning("RAG search_relevant timed out; skipping context")
            return []
        except Exception:  # noqa: BLE001
            log.exception("RAG search_relevant failed")
            return []

    async def _search_relevant_inner(
        self, query: str, chat_id: int, limit: int, min_similarity: float
    ) -> list[RelevantMessage]:
        embedding = (await asyncio.to_thread(self._embed_sync, [query]))[0]
        in_chat = await self._query(
            embedding, limit, where={"chat_id": chat_id}
        )
        results = [m for m in in_chat if m.similarity_score >= min_similarity]
        if len(results) < limit:
            # Top up with global hits (slightly stronger threshold to keep
            # noise from unrelated chats out).
            global_hits = await self._query(embedding, limit * 2)
            seen = {(m.chat_id, m.message_id) for m in results}
            for m in global_hits:
                key = (m.chat_id, m.message_id)
                if key in seen:
                    continue
                if m.similarity_score >= max(min_similarity, 0.65):
                    results.append(m)
                    seen.add(key)
        results.sort(key=lambda m: m.similarity_score, reverse=True)
        return results[:limit]

    async def search_cross_chat(
        self,
        query: str,
        limit: int = 3,
        min_similarity: float = 0.7,
    ) -> list[RelevantMessage]:
        """Find relevant context across ALL chats with a stricter threshold."""
        if not query or not query.strip():
            return []
        if not self._available and not await self.initialize():
            return []
        try:
            hits = await asyncio.wait_for(
                self._query(
                    (await asyncio.to_thread(self._embed_sync, [query]))[0],
                    limit * 3,
                ),
                timeout=SEARCH_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            log.warning("RAG search_cross_chat timed out; skipping context")
            return []
        except Exception:  # noqa: BLE001
            log.exception("RAG search_cross_chat failed")
            return []
        filtered = [m for m in hits if m.similarity_score >= min_similarity]
        filtered.sort(key=lambda m: m.similarity_score, reverse=True)
        return filtered[:limit]

    async def _query(
        self, embedding: list[float], limit: int, where: Optional[dict] = None
    ) -> list[RelevantMessage]:
        kwargs: dict = {
            "query_embeddings": [embedding],
            "n_results": max(1, limit),
        }
        if where:
            kwargs["where"] = where
        raw = await asyncio.to_thread(self._collection.query, **kwargs)
        documents = (raw.get("documents") or [[]])[0]
        metadatas = (raw.get("metadatas") or [[]])[0]
        distances = (raw.get("distances") or [[]])[0]
        out: list[RelevantMessage] = []
        for doc, meta, dist in zip(documents, metadatas, distances):
            meta = meta or {}
            out.append(
                RelevantMessage(
                    text=doc or "",
                    sender_name=str(meta.get("sender_name", "")),
                    timestamp=str(meta.get("timestamp", "")),
                    similarity_score=max(0.0, 1.0 - float(dist)),
                    chat_name=str(meta.get("chat_name", "")),
                    chat_id=int(meta.get("chat_id", 0) or 0),
                    message_id=int(meta.get("message_id", 0) or 0),
                )
            )
        return out

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------
    async def delete_message(self, message_id: int) -> None:
        if not self._available:
            return
        try:
            await asyncio.to_thread(
                self._collection.delete, ids=[str(message_id)]
            )
        except Exception:  # noqa: BLE001
            log.exception("RAG delete_message failed for id=%s", message_id)

    async def clear(self) -> None:
        """Drop and recreate the collection, and reset rag_indexed flags."""
        from .database import Message, SessionLocal
        from sqlalchemy import update

        if not self._available and not await self.initialize():
            raise RuntimeError(self._last_error or "RAG недоступен")
        await asyncio.to_thread(self._recreate_collection)
        self._last_indexed_at = None
        async with SessionLocal() as session:
            await session.execute(update(Message).values(rag_indexed=False))
            await session.commit()

    async def count(self) -> int:
        if not self._available:
            return 0
        try:
            return int(await asyncio.to_thread(self._collection.count))
        except Exception:  # noqa: BLE001
            log.exception("RAG count failed")
            return 0

    async def get_stats(self) -> RAGStats:
        total = await self.count()
        size_mb = 0.0
        try:
            size_bytes = sum(
                f.stat().st_size
                for f in settings.chroma_db_dir.rglob("*")
                if f.is_file()
            )
            size_mb = round(size_bytes / (1024 * 1024), 2)
        except Exception:  # noqa: BLE001
            pass
        return RAGStats(
            total_indexed=total,
            collection_size_mb=size_mb,
            last_indexed_at=self._last_indexed_at,
        )


def _setting_bool(value: str) -> bool:
    return value in ("1", "true", "True", "yes", "on")


def _setting_float(value: str, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _setting_int(value: str, default: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


async def get_rag_context(
    session, query: str, chat_id: Optional[int]
) -> tuple[str, list[RelevantMessage]]:
    """Resolve RAG settings, run the search, and format a prompt block.

    Returns ``(context_string, messages)``. The string is empty when RAG is
    disabled, unavailable, or nothing relevant was found.
    """
    from .database import get_setting

    if not query or not query.strip():
        return "", []
    if not _setting_bool(await get_setting(session, "rag_enabled", "1")):
        return "", []
    if not rag_engine.available and not await rag_engine.initialize():
        return "", []

    min_similarity = _setting_float(
        await get_setting(session, "rag_min_similarity", "0.6"), 0.6
    )
    max_results = _setting_int(
        await get_setting(session, "rag_max_results", "5"), 5
    )
    cross_chat = _setting_bool(
        await get_setting(session, "rag_search_cross_chat", "1")
    )
    cross_min = _setting_float(
        await get_setting(session, "rag_cross_chat_min_similarity", "0.7"), 0.7
    )

    messages: list[RelevantMessage] = []
    if chat_id is not None:
        messages = await rag_engine.search_relevant(
            query, chat_id, limit=max_results, min_similarity=min_similarity
        )
    if cross_chat:
        seen = {(m.chat_id, m.message_id) for m in messages}
        for m in await rag_engine.search_cross_chat(
            query, limit=3, min_similarity=cross_min
        ):
            if (m.chat_id, m.message_id) not in seen:
                messages.append(m)
                seen.add((m.chat_id, m.message_id))

    return build_rag_context(messages), messages


def _iso_or_empty(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value)


rag_engine = RAGEngine()
