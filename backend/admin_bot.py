"""Telegram admin panel.

Same bot, second role: admin commands accepted ONLY from OWNER_CHAT_ID.
Every handler is guarded by the IsOwner filter; non-owner updates fall
through to the regular business-mode handlers in bot.py.
"""
from __future__ import annotations

import html
import json
import logging
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import Optional

from aiogram import Bot, Dispatcher
from aiogram.filters import BaseFilter, Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import func, select

from .config import settings
from .database import (
    AdminNotification,
    CreatedMeeting,
    Message as DbMessage,
    QualityLog,
    SessionLocal,
    TrainingPair,
    get_setting,
    set_setting,
)
from .delay import get_delay_settings, save_delay_settings
from .schedule import (
    ScheduleSettings,
    get_schedule_settings,
    save_schedule_settings,
    schedule_status,
)

log = logging.getLogger(__name__)

_TRUE = {"1", "true", "True", "yes", "on"}
RATE_LIMIT_PER_MIN = 30


# --------------------------------------------------------------------------
# FSM states
# --------------------------------------------------------------------------
class AdminStates(StatesGroup):
    waiting_custom_reply = State()
    waiting_correction = State()
    waiting_schedule_input = State()
    waiting_whitelist_id = State()


# --------------------------------------------------------------------------
# Admin-action audit log
# --------------------------------------------------------------------------
admin_log = logging.getLogger("admin_actions")


def _setup_admin_log() -> None:
    if admin_log.handlers:
        return
    admin_log.setLevel(logging.INFO)
    try:
        handler = logging.FileHandler(
            settings.logs_dir / "admin_actions.log", encoding="utf-8"
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s | %(message)s")
        )
        admin_log.addHandler(handler)
        admin_log.propagate = False
    except Exception:  # noqa: BLE001
        log.exception("cannot set up admin_actions.log")


# --------------------------------------------------------------------------
# Security: owner resolution + filter
# --------------------------------------------------------------------------
async def get_owner_chat_id() -> str:
    """Resolve the owner chat id: DB setting first, then .env fallback."""
    try:
        async with SessionLocal() as session:
            stored = await get_setting(session, "owner_chat_id", "")
    except Exception:  # noqa: BLE001
        stored = ""
    return (stored or settings.owner_chat_id or "").strip()


async def is_owner(message: Message) -> bool:
    owner = await get_owner_chat_id()
    if not owner:
        return False
    return str(message.chat.id) == str(owner)


def _chat_id_of(event) -> Optional[int]:
    if isinstance(event, Message):
        return event.chat.id
    if isinstance(event, CallbackQuery):
        return event.message.chat.id if event.message else None
    return None


class IsOwner(BaseFilter):
    """Guards every admin handler — if not owner, silently let it fall through."""

    async def __call__(self, event) -> bool:
        chat_id = _chat_id_of(event)
        if chat_id is None:
            return False
        owner = await get_owner_chat_id()
        if not owner:
            log.warning("admin panel: OWNER_CHAT_ID is empty, ignoring update")
            return False
        return str(chat_id) == str(owner)


# --------------------------------------------------------------------------
# Rate limiting
# --------------------------------------------------------------------------
_rate_hits: dict[str, deque] = {}


def _rate_limited(chat_id) -> bool:
    now = time.monotonic()
    dq = _rate_hits.setdefault(str(chat_id), deque())
    while dq and now - dq[0] > 60.0:
        dq.popleft()
    if len(dq) >= RATE_LIMIT_PER_MIN:
        return True
    dq.append(now)
    return False


async def _guard(event, action: str) -> bool:
    """Rate-limit + audit. Returns False if the action must be dropped."""
    chat_id = _chat_id_of(event)
    if _rate_limited(chat_id):
        admin_log.warning("chat=%s RATE-LIMITED action=%s", chat_id, action)
        return False
    admin_log.info("chat=%s action=%s", chat_id, action)
    return True


# --------------------------------------------------------------------------
# Misc helpers
# --------------------------------------------------------------------------
def _esc(text: str) -> str:
    return html.escape(text or "")


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _relative(ts: Optional[datetime]) -> str:
    if ts is None:
        return ""
    delta = datetime.utcnow() - ts
    secs = int(delta.total_seconds())
    if secs < 60:
        return "только что"
    if secs < 3600:
        return f"{secs // 60} мин"
    if secs < 86400:
        return f"{secs // 3600} ч"
    return f"{secs // 86400} дн"


def _fmt_local(ts: Optional[datetime], tz_name: str, fmt: str = "%H:%M") -> str:
    """Format a naive-UTC timestamp in the configured timezone."""
    if ts is None:
        return ""
    aware = ts.replace(tzinfo=timezone.utc)
    try:
        aware = aware.astimezone(ZoneInfo(tz_name))
    except (ZoneInfoNotFoundError, ValueError):
        pass
    return aware.strftime(fmt)


def _today_start() -> datetime:
    return datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)


# In-memory cache of generated reply variants, keyed by message DB id.
_variant_cache: dict[int, list[str]] = {}


# --------------------------------------------------------------------------
# Inline keyboards
# --------------------------------------------------------------------------
def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("📊 Статус", "m:status"), _btn("📋 Очередь", "m:pending")],
            [_btn("⚙️ Настройки", "m:settings"), _btn("📈 Статы", "m:stats")],
            [_btn("⏸ Пауза", "m:pause"), _btn("▶️ Резюме", "m:resume")],
            [_btn("🎤 Модели", "m:models"), _btn("❓ Помощь", "m:help")],
        ]
    )


def _back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[_btn("← Назад", "m:main")]]
    )


async def settings_menu_kb(session) -> InlineKeyboardMarkup:
    auto = (
        await get_setting(session, "auto_reply", "1" if settings.auto_reply else "0")
    ) in _TRUE
    quality = (
        await get_setting(session, "quality_filter_enabled", "1")
    ) in _TRUE
    sched = await get_schedule_settings(session)
    delay = await get_delay_settings(session)
    sched_label = (
        f"{sched.start[:2]}-{sched.end[:2]}" if sched.enabled else "ВЫКЛ"
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn(f"🔄 Авто-ответ: {'ВКЛ' if auto else 'ВЫКЛ'}", "set:autoreply")],
            [_btn(f"⏰ Расписание: {sched_label}", "set:schedule")],
            [_btn(f"🎯 Фильтр качества: {'ВКЛ' if quality else 'ВЫКЛ'}", "set:quality")],
            [_btn(f"⏱ Задержка: {'ВКЛ' if delay.enabled else 'ВЫКЛ'}", "set:delay")],
            [_btn("← Назад", "m:main")],
        ]
    )


def _pending_item_kb(msg_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn("💬 Ответить", f"pend:reply:{msg_id}"),
                _btn("🤖 Авто", f"pend:auto:{msg_id}"),
                _btn("⏭ Пропустить", f"pend:skip:{msg_id}"),
            ]
        ]
    )


def _variants_kb(msg_id: int, count: int) -> InlineKeyboardMarkup:
    digits = ["1️⃣", "2️⃣", "3️⃣"]
    rows: list[list[InlineKeyboardButton]] = []
    row = [_btn(digits[i], f"var:{msg_id}:{i}") for i in range(min(count, 3))]
    if row:
        rows.append(row)
    rows.append([_btn("✏️ Свой ответ", f"var:{msg_id}:custom")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _train_kb(msg_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn("✅ Да", f"train:yes:{msg_id}"),
                _btn("❌ Нет", f"train:no:{msg_id}"),
            ]
        ]
    )


def _feedback_kb(msg_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn("👍 Хорошо", f"fb:good:{msg_id}"),
                _btn("👎 Плохо", f"fb:bad:{msg_id}"),
                _btn("✏️ Исправить", f"fb:fix:{msg_id}"),
            ]
        ]
    )


async def model_menu_kb(session) -> InlineKeyboardMarkup:
    from .whisper_engine import whisper_engine

    rows = []
    if not whisper_engine.is_loaded:
        rows.append([_btn("📥 Загрузить Whisper", "mdl:whisper_load")])
    else:
        rows.append([_btn("📤 Выгрузить Whisper", "mdl:whisper_unload")])
    rows.append([_btn("← Назад", "m:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# --------------------------------------------------------------------------
# Text builders
# --------------------------------------------------------------------------
async def build_status_text() -> str:
    from .bot import telegram_service
    from .llm_engine import get_client
    from .whisper_engine import whisper_engine

    try:
        llm_ok = await get_client().health()
    except Exception:  # noqa: BLE001
        llm_ok = False
    bot_ok = telegram_service.is_configured

    async with SessionLocal() as session:
        auto = (
            await get_setting(
                session, "auto_reply", "1" if settings.auto_reply else "0"
            )
        ) in _TRUE
        lazy = (
            await get_setting(session, "whisper_lazy_load", "0")
        ) in _TRUE
        sched = await get_schedule_settings(session)
        today = _today_start()
        received = (
            await session.execute(
                select(func.count(DbMessage.id)).where(
                    DbMessage.is_mine == False,  # noqa: E712
                    DbMessage.timestamp >= today,
                )
            )
        ).scalar() or 0
        sent = (
            await session.execute(
                select(func.count(DbMessage.id)).where(
                    DbMessage.is_mine == True,  # noqa: E712
                    DbMessage.timestamp >= today,
                )
            )
        ).scalar() or 0
        queued = (
            await session.execute(
                select(func.count(DbMessage.id)).where(
                    DbMessage.is_mine == False,  # noqa: E712
                    DbMessage.replied == False,  # noqa: E712
                    DbMessage.deleted == False,  # noqa: E712
                )
            )
        ).scalar() or 0
        rejected = (
            await session.execute(
                select(func.count(QualityLog.id)).where(
                    QualityLog.timestamp >= today
                )
            )
        ).scalar() or 0

    if whisper_engine.is_loaded:
        whisper_line = "Whisper: ✅ загружен"
    elif lazy:
        whisper_line = "Whisper: ⏸ выгружен (lazy)"
    else:
        whisper_line = "Whisper: ⏸ выгружен"

    status = schedule_status(sched)
    if not sched.enabled:
        sched_line = "⏰ Расписание: не ограничено"
    else:
        mark = "✅" if status["active"] else "🔴"
        sched_line = f"⏰ Расписание: {sched.start}–{sched.end} {mark}"

    return (
        "🤖 <b>Статус системы</b>\n"
        "──────────────────\n"
        f"LM Studio: {'✅ онлайн' if llm_ok else '🔴 офлайн'}\n"
        f"{whisper_line}\n"
        f"Бот: {'✅ активен' if bot_ok else '🔴 не настроен'}\n\n"
        "📊 Сегодня:\n"
        f"├ Получено: {received}\n"
        f"├ Отправлено: {sent}\n"
        f"├ В очереди: {queued}\n"
        f"└ Отклонено: {rejected}\n\n"
        f"{sched_line}\n"
        f"🔄 Авто-ответ: {'включён' if auto else 'выключен'}"
    )


async def build_stats_text(period: str) -> str:
    period = (period or "today").lower()
    now = datetime.utcnow()
    if period == "week":
        since = now - timedelta(days=7)
        label = "за неделю"
    elif period == "month":
        since = now - timedelta(days=30)
        label = "за месяц"
    else:
        period = "today"
        since = _today_start()
        label = "сегодня"

    async with SessionLocal() as session:
        received = (
            await session.execute(
                select(func.count(DbMessage.id)).where(
                    DbMessage.is_mine == False,  # noqa: E712
                    DbMessage.timestamp >= since,
                )
            )
        ).scalar() or 0
        sent = (
            await session.execute(
                select(func.count(DbMessage.id)).where(
                    DbMessage.is_mine == True,  # noqa: E712
                    DbMessage.timestamp >= since,
                )
            )
        ).scalar() or 0
        replied = (
            await session.execute(
                select(func.count(DbMessage.id)).where(
                    DbMessage.is_mine == False,  # noqa: E712
                    DbMessage.replied == True,  # noqa: E712
                    DbMessage.timestamp >= since,
                )
            )
        ).scalar() or 0
        rejected = (
            await session.execute(
                select(func.count(QualityLog.id)).where(
                    QualityLog.timestamp >= since
                )
            )
        ).scalar() or 0

    return (
        f"📈 <b>Статистика ({label})</b>\n"
        "──────────────────\n"
        f"├ Получено: {received}\n"
        f"├ Отправлено: {sent}\n"
        f"├ Отвечено: {replied}\n"
        f"└ Отклонено фильтром: {rejected}"
    )


def _help_text() -> str:
    return (
        "❓ <b>Команды админ-панели</b>\n"
        "──────────────────\n"
        "/start — главное меню\n"
        "/status — статус системы\n"
        "/pause — поставить бота на паузу\n"
        "/resume — возобновить\n"
        "/autoreply on|off — авто-ответ\n"
        "/schedule on 9 23 — задать расписание\n"
        "/schedule off — выключить расписание\n"
        "/stats [today|week|month] — статистика\n"
        "/pending — очередь сообщений\n"
        "/reply &lt;id&gt; &lt;текст&gt; — ручной ответ\n"
        "/model status|load|unload — управление моделями\n"
        "/whitelist add|remove|list &lt;chat_id&gt; — наблюдаемые чаты\n"
        "/calendar — состояние календаря и свободные слоты\n"
        "/help — эта справка"
    )


# --------------------------------------------------------------------------
# Command handlers
# --------------------------------------------------------------------------
async def cmd_start(message: Message, state: FSMContext) -> None:
    if not await _guard(message, "/start"):
        return
    await state.clear()
    await message.answer(
        "🤖 <b>AI Assistant Admin Panel</b>\n"
        "──────────────────\n"
        "Добро пожаловать. Выбери раздел:",
        reply_markup=main_menu_kb(),
    )


async def cmd_status(message: Message) -> None:
    if not await _guard(message, "/status"):
        return
    await message.answer(await build_status_text(), reply_markup=_back_kb())


async def cmd_pause(message: Message) -> None:
    if not await _guard(message, "/pause"):
        return
    async with SessionLocal() as session:
        await set_setting(session, "auto_reply", "0")
    await message.answer("⏸ Бот на паузе")


async def cmd_resume(message: Message) -> None:
    if not await _guard(message, "/resume"):
        return
    async with SessionLocal() as session:
        await set_setting(session, "auto_reply", "1")
    await message.answer("▶️ Возобновлён")


async def cmd_autoreply(message: Message, command: CommandObject) -> None:
    if not await _guard(message, "/autoreply"):
        return
    arg = (command.args or "").strip().lower()
    if arg not in ("on", "off"):
        await message.answer("Использование: /autoreply on|off")
        return
    async with SessionLocal() as session:
        await set_setting(session, "auto_reply", "1" if arg == "on" else "0")
    await message.answer(
        f"🔄 Авто-ответ {'включён' if arg == 'on' else 'выключен'}"
    )


def _apply_schedule_args(
    current: ScheduleSettings, args: str
) -> tuple[Optional[ScheduleSettings], str]:
    parts = (args or "").split()
    if not parts:
        return None, "Использование: /schedule on 9 23  или  /schedule off"
    mode = parts[0].lower()
    if mode == "off":
        return (
            ScheduleSettings(
                enabled=False,
                timezone=current.timezone,
                days=current.days,
                start=current.start,
                end=current.end,
            ),
            "⏰ Расписание выключено",
        )
    if mode == "on":
        start, end = current.start, current.end
        if len(parts) >= 3:
            try:
                sh, eh = int(parts[1]), int(parts[2])
            except ValueError:
                return None, "Часы должны быть числами: /schedule on 9 23"
            if not (0 <= sh <= 23 and 0 <= eh <= 23):
                return None, "Часы должны быть от 0 до 23"
            start, end = f"{sh:02d}:00", f"{eh:02d}:00"
        return (
            ScheduleSettings(
                enabled=True,
                timezone=current.timezone,
                days=current.days,
                start=start,
                end=end,
            ),
            f"⏰ Расписание: {start}–{end} ✅",
        )
    return None, "Использование: /schedule on 9 23  или  /schedule off"


async def cmd_schedule(message: Message, command: CommandObject) -> None:
    if not await _guard(message, "/schedule"):
        return
    async with SessionLocal() as session:
        current = await get_schedule_settings(session)
        new_schedule, reply = _apply_schedule_args(current, command.args or "")
        if new_schedule is None:
            await message.answer(reply)
            return
        await save_schedule_settings(session, new_schedule)
    await message.answer(reply)


async def cmd_stats(message: Message, command: CommandObject) -> None:
    if not await _guard(message, "/stats"):
        return
    period = (command.args or "today").strip().lower() or "today"
    await message.answer(await build_stats_text(period), reply_markup=_back_kb())


async def _send_pending_queue(target: Message) -> None:
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(DbMessage)
                .where(
                    DbMessage.is_mine == False,  # noqa: E712
                    DbMessage.replied == False,  # noqa: E712
                    DbMessage.deleted == False,  # noqa: E712
                )
                .order_by(DbMessage.timestamp.desc())
                .limit(10)
            )
        ).scalars().all()
    if not rows:
        await target.answer("📋 Очередь пуста", reply_markup=_back_kb())
        return
    await target.answer(f"📋 <b>В очереди: {len(rows)}</b>")
    for m in rows:
        text = (m.text or "").strip() or "(без текста)"
        await target.answer(
            f"📨 <b>{_esc(m.sender_name or m.chat_name)}</b> ({_relative(m.timestamp)}):\n"
            f"«{_esc(text[:400])}»",
            reply_markup=_pending_item_kb(m.id),
        )


async def cmd_pending(message: Message) -> None:
    if not await _guard(message, "/pending"):
        return
    await _send_pending_queue(message)


async def cmd_reply(message: Message, command: CommandObject) -> None:
    if not await _guard(message, "/reply"):
        return
    args = (command.args or "").strip()
    parts = args.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Использование: /reply &lt;message_id&gt; &lt;текст&gt;")
        return
    try:
        msg_id = int(parts[0])
    except ValueError:
        await message.answer("message_id должен быть числом")
        return
    ok, info = await _send_manual_reply(msg_id, parts[1])
    await message.answer(info)


async def cmd_model(message: Message, command: CommandObject) -> None:
    if not await _guard(message, "/model"):
        return
    from .llm_engine import get_client
    from .whisper_engine import whisper_engine

    arg = (command.args or "status").strip().lower() or "status"
    if arg == "unload":
        whisper_engine.unload()
        await message.answer("📤 Whisper выгружен из памяти")
        return
    if arg == "load":
        async with SessionLocal() as session:
            model_name = await get_setting(session, "whisper_model", "large-v3")
        loaded = whisper_engine.load(model_name)
        await message.answer(
            f"📥 Whisper {'загружен' if loaded else 'не удалось загрузить'} ({model_name})"
        )
        return
    async with SessionLocal() as session:
        whisper_model = await get_setting(session, "whisper_model", "large-v3")
    await message.answer(
        "🧠 <b>Модели</b>\n"
        "──────────────────\n"
        f"LM Studio: {_esc(get_client().model)}\n"
        f"Whisper: {_esc(whisper_model)} "
        f"({'загружен' if whisper_engine.is_loaded else 'выгружен'})",
        reply_markup=await model_menu_kb(None),
    )


async def cmd_whitelist(message: Message, command: CommandObject, state: FSMContext) -> None:
    if not await _guard(message, "/whitelist"):
        return
    parts = (command.args or "").split()
    action = parts[0].lower() if parts else ""
    if action == "list":
        async with SessionLocal() as session:
            csv = await get_setting(session, "monitored_chats", "")
        ids = [x for x in csv.split(",") if x.strip()]
        await message.answer(
            "📋 Наблюдаемые чаты:\n" + ("\n".join(ids) if ids else "(все чаты)")
        )
        return
    if action not in ("add", "remove"):
        await message.answer(
            "Использование: /whitelist add|remove|list &lt;chat_id&gt;"
        )
        return
    if len(parts) < 2:
        await state.set_state(AdminStates.waiting_whitelist_id)
        await state.update_data(whitelist_action=action)
        await message.answer(f"Введи chat_id для действия «{action}»:")
        return
    await message.answer(await _apply_whitelist(action, parts[1]))


async def cmd_help(message: Message) -> None:
    if not await _guard(message, "/help"):
        return
    await message.answer(_help_text(), reply_markup=_back_kb())


# --------------------------------------------------------------------------
# Calendar (CalDAV)
# --------------------------------------------------------------------------
def _calendar_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn("📋 События на неделю", "cal:events"),
                _btn("🕐 Свободные слоты", "cal:slots"),
            ],
            [_btn("← Назад", "m:main")],
        ]
    )


def _fmt_event_dt(dt) -> str:
    try:
        return dt.strftime("%d.%m %H:%M")
    except Exception:  # noqa: BLE001
        return str(dt)


async def build_calendar_text() -> str:
    from .calendar_engine import calendar_engine

    status = await calendar_engine.get_status()
    if not status.get("connected"):
        return (
            "📅 <b>Календарь</b>\n"
            "──────────────\n"
            "Статус: 🔴 не подключён\n"
            "Настрой подключение в веб-панели: Настройки → Календарь"
        )
    next_event = status.get("next_event")
    if next_event:
        next_line = (
            f"Следующее: {_esc(str(next_event.get('title', '')))} "
            f"в {_fmt_event_dt(next_event.get('start'))}"
        )
    else:
        next_line = "Следующее: ближайших событий нет"
    return (
        "📅 <b>Календарь</b>\n"
        "──────────────\n"
        f"Статус: ✅ подключён ({_esc(str(status.get('calendar') or '—'))})\n"
        f"Сегодня: {status.get('events_today', 0)} событ.\n"
        f"{next_line}"
    )


async def cmd_calendar(message: Message) -> None:
    if not await _guard(message, "/calendar"):
        return
    await message.answer(
        await build_calendar_text(), reply_markup=_calendar_menu_kb()
    )


async def _handle_calendar(cq: CallbackQuery, parts: list[str]) -> None:
    from .calendar_engine import calendar_engine
    from datetime import datetime as _dt, timedelta as _td

    action = parts[1] if len(parts) > 1 else ""
    if not calendar_engine.is_connected:
        await _edit_or_send(
            cq, await build_calendar_text(), _calendar_menu_kb()
        )
        return
    if action == "events":
        events = await calendar_engine.get_events(
            _dt.now(), _dt.now() + _td(days=7)
        )
        if not events:
            body = "На ближайшую неделю событий нет 🎉"
        else:
            body = "\n".join(
                f"• {_fmt_event_dt(e['start'])} — {_esc(str(e['title']))}"
                for e in events[:15]
            )
        await _edit_or_send(
            cq,
            "📋 <b>События на неделю</b>\n──────────────\n" + body,
            _calendar_menu_kb(),
        )
    elif action == "slots":
        slots = await calendar_engine.get_free_slots()
        if not slots:
            body = "Свободных слотов не найдено"
        else:
            body = "\n".join(f"• {_esc(s['label'])}" for s in slots)
        await _edit_or_send(
            cq,
            "🕐 <b>Свободные слоты</b>\n──────────────\n" + body,
            _calendar_menu_kb(),
        )
    elif action == "del" and len(parts) >= 3:
        uid = ":".join(parts[2:])
        around = None
        try:
            async with SessionLocal() as session:
                row = (
                    await session.execute(
                        select(CreatedMeeting).where(
                            CreatedMeeting.calendar_uid == uid
                        )
                    )
                ).scalar_one_or_none()
                if row:
                    around = row.start_time
        except Exception:  # noqa: BLE001
            log.exception("lookup CreatedMeeting failed for uid=%s", uid)
        ok = await calendar_engine.delete_event(uid, around=around)
        await _edit_or_send(
            cq,
            "🗑 Событие удалено из календаря" if ok
            else "Не удалось удалить событие",
        )
    else:
        await _edit_or_send(
            cq, await build_calendar_text(), _calendar_menu_kb()
        )


# --------------------------------------------------------------------------
# Shared actions
# --------------------------------------------------------------------------
async def _apply_whitelist(action: str, raw_id: str) -> str:
    raw_id = raw_id.strip()
    try:
        chat_id = int(raw_id)
    except ValueError:
        return "chat_id должен быть числом"
    async with SessionLocal() as session:
        csv = await get_setting(session, "monitored_chats", "")
        ids = [x.strip() for x in csv.split(",") if x.strip()]
        if action == "add":
            if str(chat_id) not in ids:
                ids.append(str(chat_id))
            result = f"➕ Чат {chat_id} добавлен в наблюдаемые"
        else:
            ids = [x for x in ids if x != str(chat_id)]
            result = f"➖ Чат {chat_id} убран из наблюдаемых"
        await set_setting(session, "monitored_chats", ",".join(ids))
    return result


async def _send_manual_reply(
    msg_id: int, text: str, save_pair: bool = True
) -> tuple[bool, str]:
    from .bot import telegram_service

    if not telegram_service.is_configured:
        return False, "Бот не настроен"
    async with SessionLocal() as session:
        msg = (
            await session.execute(select(DbMessage).where(DbMessage.id == msg_id))
        ).scalar_one_or_none()
        if not msg:
            return False, "Сообщение не найдено"
        chat_id = msg.chat_id
        reply_to = msg.message_id
        biz = msg.business_connection_id
        original_text = msg.text
        chat_for_pair = msg.chat_id
    try:
        await telegram_service.send_and_record(
            chat_id,
            text,
            reply_to=reply_to,
            original_id=msg_id,
            business_connection_id=biz,
        )
    except Exception as e:  # noqa: BLE001
        log.exception("admin manual reply failed")
        return False, f"Не удалось отправить: {e}"
    if save_pair:
        async with SessionLocal() as session:
            session.add(
                TrainingPair(
                    input_text=original_text,
                    output_text=text,
                    chat_id=chat_for_pair,
                    timestamp=datetime.utcnow(),
                    feedback="good",
                )
            )
            msg = (
                await session.execute(
                    select(DbMessage).where(DbMessage.id == msg_id)
                )
            ).scalar_one_or_none()
            if msg:
                msg.admin_reviewed = True
            await session.commit()
    return True, "✅ Ответ отправлен"


async def _generate_variants_for(msg_id: int) -> tuple[list[str], str]:
    from .bot import telegram_service

    async with SessionLocal() as session:
        msg = (
            await session.execute(select(DbMessage).where(DbMessage.id == msg_id))
        ).scalar_one_or_none()
        if not msg:
            return [], "not_found"
        text = msg.transcription or msg.text
        sender = msg.sender_name
        chat_id = msg.chat_id
        is_voice = bool(getattr(msg, "is_voice", False))
    variants, reason = await telegram_service._generate_variants(
        text, sender, chat_id, is_voice=is_voice
    )
    return variants, reason


# --------------------------------------------------------------------------
# FSM message handlers
# --------------------------------------------------------------------------
async def on_custom_reply(message: Message, state: FSMContext) -> None:
    if not await _guard(message, "fsm:custom_reply"):
        return
    data = await state.get_data()
    msg_id = data.get("msg_id")
    await state.clear()
    if not msg_id:
        await message.answer("Не понял, к какому сообщению ответ. Открой /pending заново.")
        return
    ok, info = await _send_manual_reply(int(msg_id), message.text or "")
    await message.answer(info)


async def on_correction(message: Message, state: FSMContext) -> None:
    if not await _guard(message, "fsm:correction"):
        return
    data = await state.get_data()
    msg_id = data.get("msg_id")
    await state.clear()
    if not msg_id:
        await message.answer("Не понял, что исправлять.")
        return
    correction = (message.text or "").strip()
    async with SessionLocal() as session:
        msg = (
            await session.execute(
                select(DbMessage).where(DbMessage.id == int(msg_id))
            )
        ).scalar_one_or_none()
        if not msg:
            await message.answer("Сообщение не найдено")
            return
        msg.admin_feedback = "bad"
        msg.admin_correction = correction
        msg.admin_reviewed = True
        session.add(
            TrainingPair(
                input_text=msg.text,
                output_text=correction,
                chat_id=msg.chat_id,
                timestamp=datetime.utcnow(),
                feedback="good",
            )
        )
        await session.commit()
    await message.answer("✅ Исправление сохранено как обучающая пара")


async def on_schedule_input(message: Message, state: FSMContext) -> None:
    if not await _guard(message, "fsm:schedule_input"):
        return
    await state.clear()
    raw = (message.text or "").strip()
    parts = raw.split()
    if len(parts) < 2:
        await message.answer("Нужно два числа: например «9 23»")
        return
    async with SessionLocal() as session:
        current = await get_schedule_settings(session)
        new_schedule, reply = _apply_schedule_args(
            current, f"on {parts[0]} {parts[1]}"
        )
        if new_schedule is None:
            await message.answer(reply)
            return
        await save_schedule_settings(session, new_schedule)
    await message.answer(reply)


async def on_whitelist_id(message: Message, state: FSMContext) -> None:
    if not await _guard(message, "fsm:whitelist_id"):
        return
    data = await state.get_data()
    action = data.get("whitelist_action", "add")
    await state.clear()
    await message.answer(await _apply_whitelist(action, message.text or ""))


# --------------------------------------------------------------------------
# Callback query handler
# --------------------------------------------------------------------------
async def _edit_or_send(cq: CallbackQuery, text: str, kb=None) -> None:
    try:
        await cq.message.edit_text(text, reply_markup=kb)
    except Exception:  # noqa: BLE001
        await cq.message.answer(text, reply_markup=kb)


async def on_callback(cq: CallbackQuery, state: FSMContext) -> None:
    if not await _guard(cq, f"cb:{cq.data}"):
        await cq.answer()
        return
    data = cq.data or ""
    parts = data.split(":")
    kind = parts[0]

    try:
        if kind == "m":
            await _handle_menu(cq, parts[1] if len(parts) > 1 else "")
        elif kind == "set":
            await _handle_settings_toggle(cq, parts[1] if len(parts) > 1 else "", state)
        elif kind == "pend":
            await _handle_pending(cq, parts, state)
        elif kind == "var":
            await _handle_variant(cq, parts, state)
        elif kind == "train":
            await _handle_train(cq, parts)
        elif kind == "fb":
            await _handle_feedback(cq, parts, state)
        elif kind == "mdl":
            await _handle_model(cq, parts[1] if len(parts) > 1 else "")
        elif kind == "cal":
            await _handle_calendar(cq, parts)
    except Exception:  # noqa: BLE001
        log.exception("admin callback failed: %s", data)
    await cq.answer()


async def _handle_menu(cq: CallbackQuery, action: str) -> None:
    if action == "main":
        await _edit_or_send(
            cq, "🤖 <b>AI Assistant Admin Panel</b>", main_menu_kb()
        )
    elif action == "status":
        await _edit_or_send(cq, await build_status_text(), _back_kb())
    elif action == "pending":
        await _send_pending_queue(cq.message)
    elif action == "settings":
        async with SessionLocal() as session:
            kb = await settings_menu_kb(session)
        await _edit_or_send(cq, "⚙️ <b>Настройки</b>", kb)
    elif action == "stats":
        await _edit_or_send(cq, await build_stats_text("today"), _back_kb())
    elif action == "pause":
        async with SessionLocal() as session:
            await set_setting(session, "auto_reply", "0")
        await _edit_or_send(cq, "⏸ Бот на паузе", _back_kb())
    elif action == "resume":
        async with SessionLocal() as session:
            await set_setting(session, "auto_reply", "1")
        await _edit_or_send(cq, "▶️ Возобновлён", _back_kb())
    elif action == "models":
        from .llm_engine import get_client
        from .whisper_engine import whisper_engine

        async with SessionLocal() as session:
            whisper_model = await get_setting(session, "whisper_model", "large-v3")
            kb = await model_menu_kb(session)
        await _edit_or_send(
            cq,
            "🧠 <b>Модели</b>\n"
            "──────────────────\n"
            f"LM Studio: {_esc(get_client().model)}\n"
            f"Whisper: {_esc(whisper_model)} "
            f"({'загружен' if whisper_engine.is_loaded else 'выгружен'})",
            kb,
        )
    elif action == "help":
        await _edit_or_send(cq, _help_text(), _back_kb())


async def _handle_settings_toggle(
    cq: CallbackQuery, action: str, state: FSMContext
) -> None:
    if action == "schedule":
        await state.set_state(AdminStates.waiting_schedule_input)
        await cq.message.answer(
            "Введи часы начала и конца через пробел, например «9 23»:"
        )
        return
    async with SessionLocal() as session:
        if action == "autoreply":
            cur = (
                await get_setting(
                    session, "auto_reply", "1" if settings.auto_reply else "0"
                )
            ) in _TRUE
            await set_setting(session, "auto_reply", "0" if cur else "1")
        elif action == "quality":
            cur = (
                await get_setting(session, "quality_filter_enabled", "1")
            ) in _TRUE
            await set_setting(
                session, "quality_filter_enabled", "0" if cur else "1"
            )
        elif action == "delay":
            delay = await get_delay_settings(session)
            await save_delay_settings(
                session,
                not delay.enabled,
                delay.min_seconds,
                delay.max_seconds,
            )
        kb = await settings_menu_kb(session)
    await _edit_or_send(cq, "⚙️ <b>Настройки</b>", kb)


async def _handle_pending(
    cq: CallbackQuery, parts: list[str], state: FSMContext
) -> None:
    if len(parts) < 3:
        return
    action, msg_id = parts[1], int(parts[2])
    if action == "skip":
        async with SessionLocal() as session:
            msg = (
                await session.execute(
                    select(DbMessage).where(DbMessage.id == msg_id)
                )
            ).scalar_one_or_none()
            if msg:
                msg.pending_reason = "skipped"
                msg.admin_reviewed = True
                await session.commit()
        await _edit_or_send(cq, "⏭ Сообщение пропущено")
    elif action == "auto":
        variants, reason = await _generate_variants_for(msg_id)
        if not variants:
            await _edit_or_send(cq, f"🤖 Не удалось сгенерировать ответ ({reason})")
            return
        from .llm_engine import pick_auto_variant

        chosen = pick_auto_variant(variants) or variants[0]
        ok, info = await _send_manual_reply(msg_id, chosen)
        await _edit_or_send(
            cq, f"🤖 {info}\n✉️ «{_esc(chosen)}»" if ok else info
        )
    elif action == "reply":
        await cq.message.answer("⏳ Генерирую варианты ответа…")
        variants, reason = await _generate_variants_for(msg_id)
        if not variants:
            await cq.message.answer(
                f"Не удалось сгенерировать варианты ({reason}). "
                f"Используй ✏️ Свой ответ."
            )
            await cq.message.answer(
                "✏️ Свой ответ:",
                reply_markup=_variants_kb(msg_id, 0),
            )
            return
        _variant_cache[msg_id] = variants
        lines = [
            f"{i + 1}️⃣ {_esc(v)}" for i, v in enumerate(variants[:3])
        ]
        await cq.message.answer(
            "Выбери вариант ответа:\n" + "\n".join(lines),
            reply_markup=_variants_kb(msg_id, len(variants)),
        )


async def _handle_variant(
    cq: CallbackQuery, parts: list[str], state: FSMContext
) -> None:
    if len(parts) < 3:
        return
    msg_id = int(parts[1])
    choice = parts[2]
    if choice == "custom":
        await state.set_state(AdminStates.waiting_custom_reply)
        await state.update_data(msg_id=msg_id)
        await cq.message.answer("✏️ Напиши свой ответ одним сообщением:")
        return
    variants = _variant_cache.get(msg_id, [])
    try:
        idx = int(choice)
    except ValueError:
        return
    if idx >= len(variants):
        await _edit_or_send(cq, "Вариант устарел, открой /pending заново")
        return
    chosen = variants[idx]
    ok, info = await _send_manual_reply(msg_id, chosen, save_pair=False)
    if not ok:
        await _edit_or_send(cq, info)
        return
    _variant_cache.pop(msg_id, None)
    await _edit_or_send(
        cq,
        f"✅ Ответ отправлен: «{_esc(chosen)}»\n\n"
        "Сохранить как обучающую пару?",
        _train_kb(msg_id),
    )


async def _handle_train(cq: CallbackQuery, parts: list[str]) -> None:
    if len(parts) < 3:
        return
    decision, msg_id = parts[1], int(parts[2])
    if decision == "yes":
        async with SessionLocal() as session:
            msg = (
                await session.execute(
                    select(DbMessage).where(DbMessage.id == msg_id)
                )
            ).scalar_one_or_none()
            if msg and msg.reply_text:
                session.add(
                    TrainingPair(
                        input_text=msg.text,
                        output_text=msg.reply_text,
                        chat_id=msg.chat_id,
                        timestamp=datetime.utcnow(),
                        feedback="good",
                    )
                )
                await session.commit()
        await _edit_or_send(cq, "✅ Сохранено как обучающая пара")
    else:
        await _edit_or_send(cq, "❌ Не сохранено")


async def _handle_feedback(
    cq: CallbackQuery, parts: list[str], state: FSMContext
) -> None:
    if len(parts) < 3:
        return
    verdict, msg_id = parts[1], int(parts[2])
    if verdict == "fix":
        await state.set_state(AdminStates.waiting_correction)
        await state.update_data(msg_id=msg_id)
        await cq.message.answer("✏️ Напиши исправленный ответ:")
        return
    feedback = "good" if verdict == "good" else "bad"
    async with SessionLocal() as session:
        msg = (
            await session.execute(select(DbMessage).where(DbMessage.id == msg_id))
        ).scalar_one_or_none()
        if msg:
            msg.admin_feedback = feedback
            msg.admin_reviewed = True
            if feedback == "bad" and msg.reply_text:
                session.add(
                    TrainingPair(
                        input_text=msg.text,
                        output_text=msg.reply_text,
                        chat_id=msg.chat_id,
                        timestamp=datetime.utcnow(),
                        feedback="bad",
                    )
                )
        await _mark_notification_response(session, msg_id, verdict)
        await session.commit()
    await _edit_or_send(
        cq, "👍 Спасибо за отзыв" if feedback == "good" else "👎 Отмечено как плохой ответ"
    )


async def _mark_notification_response(session, msg_id: int, response: str) -> None:
    row = (
        await session.execute(
            select(AdminNotification)
            .where(AdminNotification.message_id == msg_id)
            .order_by(AdminNotification.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row:
        row.owner_response = response
        row.response_at = datetime.utcnow()


async def _handle_model(cq: CallbackQuery, action: str) -> None:
    from .whisper_engine import whisper_engine

    if action == "whisper_unload":
        whisper_engine.unload()
    elif action == "whisper_load":
        async with SessionLocal() as session:
            model_name = await get_setting(session, "whisper_model", "large-v3")
        whisper_engine.load(model_name)
    from .llm_engine import get_client

    async with SessionLocal() as session:
        whisper_model = await get_setting(session, "whisper_model", "large-v3")
        kb = await model_menu_kb(session)
    await _edit_or_send(
        cq,
        "🧠 <b>Модели</b>\n"
        "──────────────────\n"
        f"LM Studio: {_esc(get_client().model)}\n"
        f"Whisper: {_esc(whisper_model)} "
        f"({'загружен' if whisper_engine.is_loaded else 'выгружен'})",
        kb,
    )


# --------------------------------------------------------------------------
# Proactive notifications (called from bot.py)
# --------------------------------------------------------------------------
async def _owner_bot_and_id() -> tuple[Optional[Bot], Optional[int]]:
    from .bot import telegram_service

    if not settings.admin_bot_enabled:
        return None, None
    owner = await get_owner_chat_id()
    if not owner:
        return None, None
    if not telegram_service.is_configured or telegram_service.bot is None:
        return None, None
    try:
        return telegram_service.bot, int(owner)
    except (TypeError, ValueError):
        log.error("admin panel: invalid OWNER_CHAT_ID=%r", owner)
        return None, None


async def _log_notification(notif_type: str, message_id: Optional[int]) -> None:
    try:
        async with SessionLocal() as session:
            session.add(
                AdminNotification(type=notif_type, message_id=message_id)
            )
            await session.commit()
    except Exception:  # noqa: BLE001
        log.exception("cannot log admin notification")


def _truncate(text: str, limit: int = 400) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


async def notify_auto_reply(
    chat_name: str,
    sender_name: str,
    original_text: str,
    reply_text: str,
    message_id: Optional[int] = None,
) -> None:
    """Notify the owner that the bot auto-replied, with feedback buttons."""
    bot, owner_id = await _owner_bot_and_id()
    if not bot or owner_id is None:
        return
    async with SessionLocal() as session:
        if (
            await get_setting(session, "admin_notify_auto", "1")
        ) not in _TRUE:
            return
    text = (
        "🤖 <b>Ответил автоматически</b>\n\n"
        f"👤 {_esc(sender_name or chat_name)}\n"
        f"💬 «{_esc(_truncate(original_text))}»\n"
        f"✉️ Мой ответ: «{_esc(_truncate(reply_text))}»"
    )
    kb = _feedback_kb(message_id) if message_id else None
    try:
        await bot.send_message(owner_id, text, reply_markup=kb)
        await _log_notification("auto_reply", message_id)
    except Exception as e:  # noqa: BLE001
        log.error("notify_auto_reply failed: %s", e)


async def notify_meeting_created(event: dict, chat_name: str = "") -> None:
    """Notify the owner that the assistant auto-created a calendar event."""
    bot, owner_id = await _owner_bot_and_id()
    if not bot or owner_id is None:
        return
    async with SessionLocal() as session:
        if (await get_setting(session, "caldav_notify", "1")) not in _TRUE:
            return
    sender = event.get("sender_name") or chat_name or "Собеседник"
    text = (
        "📅 <b>Создал встречу в календаре</b>\n\n"
        f"{_esc(sender)} подтвердил встречу\n"
        f"📌 {_esc(str(event.get('title', '')))}\n"
        f"🗓 {_esc(str(event.get('label', '')))}"
    )
    uid = event.get("uid") or ""
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[_btn("🗑 Удалить", f"cal:del:{uid}")]]
    ) if uid else None
    try:
        await bot.send_message(owner_id, text, reply_markup=kb)
        await _log_notification("meeting_created", None)
    except Exception as e:  # noqa: BLE001
        log.error("notify_meeting_created failed: %s", e)


async def notify_series_created(
    events: list[dict], chat_name: str = "", sender_name: str = ""
) -> None:
    """Notify the owner that the assistant created a series of events."""
    if not events:
        return
    bot, owner_id = await _owner_bot_and_id()
    if not bot or owner_id is None:
        return
    async with SessionLocal() as session:
        if (await get_setting(session, "caldav_notify", "1")) not in _TRUE:
            return
    who = sender_name or chat_name or "Собеседник"
    lines = [
        f"📌 {_esc(str(e.get('title', '')))} — {_esc(str(e.get('label', '')))}"
        for e in events
    ]
    text = (
        f"📅 <b>Создал серию встреч ({len(events)})</b>\n\n"
        f"{_esc(who)} попросил серию встреч:\n" + "\n".join(lines)
    )
    try:
        await bot.send_message(owner_id, text)
        await _log_notification("meeting_series_created", None)
    except Exception as e:  # noqa: BLE001
        log.error("notify_series_created failed: %s", e)


async def notify_meetings_cancelled(
    cancelled: list[dict],
    rescheduled: Optional[list[dict]] = None,
    chat_name: str = "",
    sender_name: str = "",
) -> None:
    """Notify the owner that the assistant deleted and/or rescheduled events."""
    cancelled = cancelled or []
    rescheduled = rescheduled or []
    if not cancelled and not rescheduled:
        return
    bot, owner_id = await _owner_bot_and_id()
    if not bot or owner_id is None:
        return
    async with SessionLocal() as session:
        if (await get_setting(session, "caldav_notify", "1")) not in _TRUE:
            return
    who = sender_name or chat_name or "Собеседник"
    blocks: list[str] = []
    if rescheduled:
        block = ["📅 <b>Перенёс встречи</b>", f"{_esc(who)} попросил перенести:"]
        for r in rescheduled:
            block.append(
                f"📌 {_esc(str(r.get('title', '')))}\n"
                f"   было: {_esc(str(r.get('old_label', '')))}\n"
                f"   стало: {_esc(str(r.get('label', '')))}"
            )
        blocks.append("\n".join(block))
    if cancelled:
        header = "🗑 <b>Также удалил</b>" if rescheduled else "🗑 <b>Удалил встречи из календаря</b>"
        prefix = "" if rescheduled else f"{_esc(who)} попросил отменить:\n"
        block = [header, prefix + "\n".join(
            f"📌 {_esc(str(c.get('title', '')))} — {_esc(str(c.get('label', '')))}"
            for c in cancelled
        )]
        blocks.append("\n".join(b for b in block if b))
    text = "\n\n".join(blocks)
    try:
        await bot.send_message(owner_id, text)
        await _log_notification("meeting_cancelled", None)
    except Exception as e:  # noqa: BLE001
        log.error("notify_meetings_cancelled failed: %s", e)


async def notify_pending(message_id: int) -> None:
    """Notify the owner about a new message awaiting manual handling."""
    bot, owner_id = await _owner_bot_and_id()
    if not bot or owner_id is None:
        return
    async with SessionLocal() as session:
        if (
            await get_setting(session, "admin_notify_pending", "1")
        ) not in _TRUE:
            return
        msg = (
            await session.execute(
                select(DbMessage).where(DbMessage.id == message_id)
            )
        ).scalar_one_or_none()
        schedule = await get_schedule_settings(session)
    if not msg or (msg.pending_reason == "schedule"):
        return
    when = _fmt_local(msg.timestamp, schedule.timezone)
    text = (
        "📨 <b>Новое сообщение</b>\n\n"
        f"👤 {_esc(msg.sender_name or msg.chat_name)}\n"
        f"💬 «{_esc(_truncate(msg.text))}»\n"
        f"⏰ {when}"
    )
    try:
        await bot.send_message(
            owner_id, text, reply_markup=_pending_item_kb(message_id)
        )
        await _log_notification("pending", message_id)
    except Exception as e:  # noqa: BLE001
        log.error("notify_pending failed: %s", e)


# Tracks schedule active/inactive transitions for pause notifications.
_last_within_schedule: Optional[bool] = None


async def on_schedule_check(within_schedule: bool) -> None:
    """Called on each incoming message; notifies once on active→inactive."""
    global _last_within_schedule
    previous = _last_within_schedule
    _last_within_schedule = within_schedule
    if previous is None or previous == within_schedule:
        return
    if not within_schedule:
        await notify_schedule_pause()


async def notify_schedule_pause() -> None:
    bot, owner_id = await _owner_bot_and_id()
    if not bot or owner_id is None:
        return
    async with SessionLocal() as session:
        queued = (
            await session.execute(
                select(func.count(DbMessage.id)).where(
                    DbMessage.is_mine == False,  # noqa: E712
                    DbMessage.replied == False,  # noqa: E712
                    DbMessage.deleted == False,  # noqa: E712
                )
            )
        ).scalar() or 0
        sched = await get_schedule_settings(session)
    status = schedule_status(sched)
    next_text = status.get("next_active_text") or "позже"
    text = (
        "⏸ <b>Бот ушёл на паузу по расписанию</b>\n"
        f"📋 В очереди: {queued}\n"
        f"Следующий период: {next_text}"
    )
    try:
        await bot.send_message(owner_id, text)
        await _log_notification("schedule_pause", None)
    except Exception as e:  # noqa: BLE001
        log.error("notify_schedule_pause failed: %s", e)


async def send_test_notification() -> bool:
    """Used by the /api/admin/notify-test endpoint."""
    bot, owner_id = await _owner_bot_and_id()
    if not bot or owner_id is None:
        return False
    try:
        await bot.send_message(
            owner_id,
            "🔔 Тестовое уведомление\nАдмин-панель настроена корректно.",
        )
        return True
    except Exception as e:  # noqa: BLE001
        log.error("send_test_notification failed: %s", e)
        return False


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------
def register(dp: Dispatcher) -> None:
    """Register admin handlers on the dispatcher.

    Must be called BEFORE the regular business-mode handlers so that owner
    commands take precedence; non-owner updates fail the IsOwner filter and
    fall through to the default handlers.
    """
    if not settings.admin_bot_enabled:
        log.info("admin panel disabled (ADMIN_BOT_ENABLED=false)")
        return

    _setup_admin_log()
    owner = IsOwner()

    # FSM state handlers (most specific first).
    dp.message.register(on_custom_reply, owner, AdminStates.waiting_custom_reply)
    dp.message.register(on_correction, owner, AdminStates.waiting_correction)
    dp.message.register(
        on_schedule_input, owner, AdminStates.waiting_schedule_input
    )
    dp.message.register(on_whitelist_id, owner, AdminStates.waiting_whitelist_id)

    # Command handlers.
    dp.message.register(cmd_start, owner, CommandStart())
    dp.message.register(cmd_status, owner, Command("status"))
    dp.message.register(cmd_pause, owner, Command("pause"))
    dp.message.register(cmd_resume, owner, Command("resume"))
    dp.message.register(cmd_autoreply, owner, Command("autoreply"))
    dp.message.register(cmd_schedule, owner, Command("schedule"))
    dp.message.register(cmd_stats, owner, Command("stats"))
    dp.message.register(cmd_pending, owner, Command("pending"))
    dp.message.register(cmd_reply, owner, Command("reply"))
    dp.message.register(cmd_model, owner, Command("model"))
    dp.message.register(cmd_whitelist, owner, Command("whitelist"))
    dp.message.register(cmd_calendar, owner, Command("calendar"))
    dp.message.register(cmd_help, owner, Command("help"))

    # Callback queries.
    dp.callback_query.register(on_callback, owner)

    log.info("admin panel handlers registered")
