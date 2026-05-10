"""SQLAlchemy models and async session for the local Telegram AI assistant."""
from __future__ import annotations

from datetime import datetime
from typing import AsyncGenerator

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .config import get_settings

settings = get_settings()
engine = create_async_engine(settings.database_url, echo=False, future=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    pass


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (UniqueConstraint("chat_id", "message_id", name="uq_chat_message"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(Integer, index=True)
    chat_name: Mapped[str] = mapped_column(String(255))
    sender_id: Mapped[int] = mapped_column(Integer, index=True)
    sender_name: Mapped[str] = mapped_column(String(255))
    is_mine: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    text: Mapped[str] = mapped_column(Text)
    timestamp: Mapped[datetime] = mapped_column(DateTime, index=True)
    message_id: Mapped[int] = mapped_column(Integer)
    reply_to_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    handled: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class TrainingPair(Base):
    __tablename__ = "training_pairs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    input_text: Mapped[str] = mapped_column(Text)
    output_text: Mapped[str] = mapped_column(Text)
    chat_id: Mapped[int] = mapped_column(Integer, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    source: Mapped[str] = mapped_column(String(32), default="auto")  # auto | approved
    used_in_training: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class TrainingRun(Base):
    __tablename__ = "training_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    pair_count: Mapped[int] = mapped_column(Integer, default=0)
    final_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    adapter_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="running")
    base_model: Mapped[str] = mapped_column(String(255), default="")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class StyleProfile(Base):
    __tablename__ = "style_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    messages_count: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class MonitoredChat(Base):
    __tablename__ = "monitored_chats"

    chat_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_name: Mapped[str] = mapped_column(String(255))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session
