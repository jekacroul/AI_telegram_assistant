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


class DialogBackup(Base):
    __tablename__ = "dialog_backups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(Integer, index=True)
    chat_name: Mapped[str] = mapped_column(String(255), default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True
    )
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    signature: Mapped[str] = mapped_column(String(64), default="")


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


class QualityLog(Base):
    __tablename__ = "quality_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True
    )
    reason: Mapped[str] = mapped_column(String(64), index=True)
    incoming_text: Mapped[str] = mapped_column(Text, default="")
    rejected_text: Mapped[str] = mapped_column(Text, default="")


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


engine = create_async_engine(settings.db_url, echo=False, future=True)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


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

    tables = set(inspector.get_table_names())
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
