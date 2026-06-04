from __future__ import annotations

from datetime import datetime
from typing import AsyncGenerator, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    event,
    inspect,
    select,
)
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .config import settings


class Base(DeclarativeBase):
    pass


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(Integer, index=True)
    chat_name: Mapped[str] = mapped_column(String(255), default="")
    chat_username: Mapped[str] = mapped_column(String(255), default="")
    sender_id: Mapped[int] = mapped_column(Integer, index=True)
    sender_name: Mapped[str] = mapped_column(String(255), default="")
    is_mine: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    text: Mapped[str] = mapped_column(Text, default="")
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True
    )
    message_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    replied: Mapped[bool] = mapped_column(Boolean, default=False)
    reply_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    pending_reason: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    business_connection_id: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True
    )
    deleted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    media_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    media_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    media_private: Mapped[bool] = mapped_column(Boolean, default=False)
    is_voice: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    voice_duration: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    voice_file_id: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    transcription: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    transcription_confidence: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True
    )
    transcription_low_confidence: Mapped[bool] = mapped_column(Boolean, default=False)
    transcription_error: Mapped[Optional[str]] = mapped_column(
        String(256), nullable=True
    )
    admin_reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    admin_feedback: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    admin_correction: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    rag_indexed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class AdminSession(Base):
    __tablename__ = "admin_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_chat_id: Mapped[str] = mapped_column(String(64), index=True, default="")
    last_active: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow
    )
    current_state: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    state_data_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class AdminNotification(Base):
    __tablename__ = "admin_notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String(32), index=True, default="")
    message_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    owner_response: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    response_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class DialogBackup(Base):
    __tablename__ = "dialog_backups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(Integer, index=True, unique=True)
    chat_name: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True
    )
    message_count: Mapped[int] = mapped_column(Integer, default=0)


class DialogBackupMessage(Base):
    __tablename__ = "dialog_backup_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    backup_id: Mapped[int] = mapped_column(
        ForeignKey("dialog_backups.id", ondelete="CASCADE"), index=True
    )
    chat_id: Mapped[int] = mapped_column(Integer, index=True)
    sender_id: Mapped[int] = mapped_column(Integer, default=0)
    sender_name: Mapped[str] = mapped_column(String(255), default="")
    is_mine: Mapped[bool] = mapped_column(Boolean, default=False)
    text: Mapped[str] = mapped_column(Text, default="")
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    message_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    edited: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    media_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    media_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    media_private: Mapped[bool] = mapped_column(Boolean, default=False)


class TrainingPair(Base):
    __tablename__ = "training_pairs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    input_text: Mapped[str] = mapped_column(Text)
    output_text: Mapped[str] = mapped_column(Text)
    chat_id: Mapped[int] = mapped_column(Integer, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    used_in_training: Mapped[bool] = mapped_column(Boolean, default=False)
    feedback: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)


class TrainingRun(Base):
    __tablename__ = "training_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    version: Mapped[int] = mapped_column(Integer, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    pair_count: Mapped[int] = mapped_column(Integer, default=0)
    final_loss: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    adapter_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(32), default="running")


class TelegramExport(Base):
    __tablename__ = "telegram_exports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_path: Mapped[str] = mapped_column(String(1024), index=True)
    parsed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    pairs_count: Mapped[int] = mapped_column(Integer, default=0)
    chats_count: Mapped[int] = mapped_column(Integer, default=0)
    date_from: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    date_to: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    avg_reply_length: Mapped[int] = mapped_column(Integer, default=0)


class DatasetBuild(Base):
    __tablename__ = "dataset_builds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    bot_pairs: Mapped[int] = mapped_column(Integer, default=0)
    export_pairs: Mapped[int] = mapped_column(Integer, default=0)
    total_pairs: Mapped[int] = mapped_column(Integer, default=0)
    weights_json: Mapped[str] = mapped_column(Text, default="{}")
    output_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    used_in_training_run_id: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )


class StyleProfile(Base):
    __tablename__ = "style_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    messages_count: Mapped[int] = mapped_column(Integer, default=0)


class ChatPersona(Base):
    __tablename__ = "chat_personas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(Integer, index=True, unique=True)
    chat_name: Mapped[str] = mapped_column(String(255), default="")
    profile_json: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    messages_count: Mapped[int] = mapped_column(Integer, default=0)


class ChatSummary(Base):
    """Rolling summary of a chat, covering messages older than the recent
    window so weeks of conversation stay in context within the token budget."""

    __tablename__ = "chat_summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(Integer, index=True, unique=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    last_message_id: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class QualityLog(Base):
    __tablename__ = "quality_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True
    )
    reason: Mapped[str] = mapped_column(String(64), index=True)
    incoming_text: Mapped[str] = mapped_column(Text, default="")
    rejected_text: Mapped[str] = mapped_column(Text, default="")


class RagIndexLog(Base):
    __tablename__ = "rag_index_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    total_messages: Mapped[int] = mapped_column(Integer, default=0)
    indexed_count: Mapped[int] = mapped_column(Integer, default=0)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True
    )


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class QuickReply(Base):
    __tablename__ = "quick_replies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    text: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(64), default="general", index=True)
    usage_count: Mapped[int] = mapped_column(Integer, default=0, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True
    )


class ReplicationRun(Base):
    __tablename__ = "replication_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="running", index=True)
    trigger: Mapped[str] = mapped_column(String(32), default="manual")
    target_path: Mapped[str] = mapped_column(String(1024), default="")
    source_bytes: Mapped[int] = mapped_column(Integer, default=0)
    copied_bytes: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    protected: Mapped[bool] = mapped_column(Boolean, default=True)


class PendingMeeting(Base):
    """A meeting proposal the bot offered in a reply, awaiting the contact's
    confirmation. ``proposed_slots_json`` holds the free slots that were
    offered so a later "да/ок/в среду" can be matched to a concrete time."""

    __tablename__ = "pending_meetings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(Integer, index=True)
    message_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    sender_name: Mapped[str] = mapped_column(String(255), default="")
    proposed_slots_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True
    )
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)


class CreatedMeeting(Base):
    """A calendar event the assistant created (auto on confirmation or
    manually from the dashboard)."""

    __tablename__ = "created_meetings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(Integer, index=True, default=0)
    calendar_uid: Mapped[str] = mapped_column(String(256), default="")
    title: Mapped[str] = mapped_column(String(512), default="")
    start_time: Mapped[datetime] = mapped_column(DateTime, index=True)
    end_time: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True
    )
    created_by: Mapped[str] = mapped_column(String(16), default="auto")


_is_sqlite = settings.db_url.startswith("sqlite")

# ``timeout`` is the sqlite3 driver-level busy timeout (seconds): when the
# database file is locked by another connection, the driver waits up to this
# long for the lock to clear instead of raising "database is locked"
# immediately. Required because RAG startup indexing writes concurrently with
# the bot saving messages and the queue reconcile.
engine = create_async_engine(
    settings.db_url,
    echo=False,
    future=True,
    connect_args={"timeout": 30} if _is_sqlite else {},
)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


if _is_sqlite:

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn, _connection_record):  # noqa: ANN001
        """Enable WAL so readers don't block the single writer, and set a
        generous busy timeout so concurrent writers wait instead of failing
        with 'database is locked'."""
        cursor = dbapi_conn.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_apply_lightweight_migrations)


def _apply_lightweight_migrations(sync_conn) -> None:
    inspector = inspect(sync_conn)
    columns = {col["name"] for col in inspector.get_columns("messages")}
    if "business_connection_id" not in columns:
        sync_conn.exec_driver_sql(
            "ALTER TABLE messages ADD COLUMN business_connection_id VARCHAR(128)"
        )
    if "deleted" not in columns:
        sync_conn.exec_driver_sql(
            "ALTER TABLE messages ADD COLUMN deleted BOOLEAN DEFAULT 0 NOT NULL"
        )
    if "pending_reason" not in columns:
        sync_conn.exec_driver_sql(
            "ALTER TABLE messages ADD COLUMN pending_reason VARCHAR(32)"
        )
    if "chat_username" not in columns:
        sync_conn.exec_driver_sql(
            "ALTER TABLE messages ADD COLUMN chat_username VARCHAR(255) DEFAULT '' NOT NULL"
        )
    if "media_type" not in columns:
        sync_conn.exec_driver_sql(
            "ALTER TABLE messages ADD COLUMN media_type VARCHAR(32)"
        )
    if "media_path" not in columns:
        sync_conn.exec_driver_sql(
            "ALTER TABLE messages ADD COLUMN media_path VARCHAR(1024)"
        )
    if "media_private" not in columns:
        sync_conn.exec_driver_sql(
            "ALTER TABLE messages ADD COLUMN media_private BOOLEAN DEFAULT 0 NOT NULL"
        )
    if "is_voice" not in columns:
        sync_conn.exec_driver_sql(
            "ALTER TABLE messages ADD COLUMN is_voice BOOLEAN DEFAULT 0 NOT NULL"
        )
    if "voice_duration" not in columns:
        sync_conn.exec_driver_sql(
            "ALTER TABLE messages ADD COLUMN voice_duration INTEGER"
        )
    if "voice_file_id" not in columns:
        sync_conn.exec_driver_sql(
            "ALTER TABLE messages ADD COLUMN voice_file_id VARCHAR(256)"
        )
    if "transcription" not in columns:
        sync_conn.exec_driver_sql(
            "ALTER TABLE messages ADD COLUMN transcription TEXT"
        )
    if "transcription_confidence" not in columns:
        sync_conn.exec_driver_sql(
            "ALTER TABLE messages ADD COLUMN transcription_confidence FLOAT"
        )
    if "transcription_low_confidence" not in columns:
        sync_conn.exec_driver_sql(
            "ALTER TABLE messages ADD COLUMN transcription_low_confidence BOOLEAN DEFAULT 0 NOT NULL"
        )
    if "transcription_error" not in columns:
        sync_conn.exec_driver_sql(
            "ALTER TABLE messages ADD COLUMN transcription_error VARCHAR(256)"
        )
    if "admin_reviewed" not in columns:
        sync_conn.exec_driver_sql(
            "ALTER TABLE messages ADD COLUMN admin_reviewed BOOLEAN DEFAULT 0 NOT NULL"
        )
    if "admin_feedback" not in columns:
        sync_conn.exec_driver_sql(
            "ALTER TABLE messages ADD COLUMN admin_feedback VARCHAR(16)"
        )
    if "admin_correction" not in columns:
        sync_conn.exec_driver_sql(
            "ALTER TABLE messages ADD COLUMN admin_correction TEXT"
        )
    if "rag_indexed" not in columns:
        sync_conn.exec_driver_sql(
            "ALTER TABLE messages ADD COLUMN rag_indexed BOOLEAN DEFAULT 0 NOT NULL"
        )

    backup_columns = {col["name"] for col in inspector.get_columns("dialog_backup_messages")}
    if "media_type" not in backup_columns:
        sync_conn.exec_driver_sql(
            "ALTER TABLE dialog_backup_messages ADD COLUMN media_type VARCHAR(32)"
        )
    if "media_path" not in backup_columns:
        sync_conn.exec_driver_sql(
            "ALTER TABLE dialog_backup_messages ADD COLUMN media_path VARCHAR(1024)"
        )
    if "media_private" not in backup_columns:
        sync_conn.exec_driver_sql(
            "ALTER TABLE dialog_backup_messages ADD COLUMN media_private BOOLEAN DEFAULT 0 NOT NULL"
        )
    if "edited" not in backup_columns:
        sync_conn.exec_driver_sql(
            "ALTER TABLE dialog_backup_messages ADD COLUMN edited BOOLEAN DEFAULT 0 NOT NULL"
        )

    dialog_backup_columns = {col["name"] for col in inspector.get_columns("dialog_backups")}
    if "updated_at" not in dialog_backup_columns:
        sync_conn.exec_driver_sql(
            "ALTER TABLE dialog_backups ADD COLUMN updated_at DATETIME"
        )
        sync_conn.exec_driver_sql(
            "UPDATE dialog_backups SET updated_at = created_at WHERE updated_at IS NULL"
        )

    # Collapse legacy versioned backups: keep only the highest-version row
    # per chat, drop everything older (and its messages).
    if "version" in dialog_backup_columns:
        sync_conn.exec_driver_sql(
            """
            DELETE FROM dialog_backup_messages
            WHERE backup_id IN (
                SELECT b.id FROM dialog_backups b
                WHERE b.id NOT IN (
                    SELECT MAX(id) FROM dialog_backups GROUP BY chat_id
                )
            )
            """
        )
        sync_conn.exec_driver_sql(
            """
            DELETE FROM dialog_backups
            WHERE id NOT IN (
                SELECT MAX(id) FROM dialog_backups GROUP BY chat_id
            )
            """
        )

    tables = set(inspector.get_table_names())
    if "replication_runs" not in tables:
        sync_conn.exec_driver_sql(
            """
            CREATE TABLE replication_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at DATETIME,
                finished_at DATETIME,
                status VARCHAR(32) DEFAULT 'running' NOT NULL,
                trigger VARCHAR(32) DEFAULT 'manual' NOT NULL,
                target_path VARCHAR(1024) DEFAULT '' NOT NULL,
                source_bytes INTEGER DEFAULT 0 NOT NULL,
                copied_bytes INTEGER DEFAULT 0 NOT NULL,
                duration_ms INTEGER DEFAULT 0 NOT NULL,
                error TEXT,
                protected BOOLEAN DEFAULT 1 NOT NULL
            )
            """
        )
    if "chat_personas" not in tables:
        sync_conn.exec_driver_sql(
            """
            CREATE TABLE chat_personas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL UNIQUE,
                chat_name VARCHAR(255) DEFAULT '' NOT NULL,
                profile_json TEXT NOT NULL,
                updated_at DATETIME,
                messages_count INTEGER DEFAULT 0 NOT NULL
            )
            """
        )
    if "quick_replies" in tables:
        qr_count = sync_conn.exec_driver_sql(
            "SELECT COUNT(*) FROM quick_replies"
        ).scalar_one()
        if qr_count == 0:
            default_replies = [
                "ок",
                "понял",
                "буду через час",
                "давай завтра",
                "перезвоню позже",
                "не могу сейчас",
                "хорошо, договорились",
            ]
            for text in default_replies:
                sync_conn.exec_driver_sql(
                    "INSERT INTO quick_replies (text, category, usage_count, created_at) VALUES (?, 'general', 0, CURRENT_TIMESTAMP)",
                    (text,),
                )


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session


async def get_setting(session: AsyncSession, key: str, default: str = "") -> str:
    result = await session.execute(select(Setting).where(Setting.key == key))
    row = result.scalar_one_or_none()
    return row.value if row else default


async def set_setting(session: AsyncSession, key: str, value: str) -> None:
    result = await session.execute(select(Setting).where(Setting.key == key))
    row = result.scalar_one_or_none()
    if row:
        row.value = value
    else:
        session.add(Setting(key=key, value=value))
    await session.commit()
