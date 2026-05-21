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
from sqlalchemy import Integer, delete, desc, func, select

from .config import settings
from .database import (
    AdminNotification,
    ChatPersona,
    CreatedMeeting,
    DialogBackup,
    DialogBackupMessage,
    Message as DbMessage,
    QualityLog,
    QuickReply,
    RagIndexLog,
    SessionLocal,
    TrainingPair,
    TrainingRun,
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
    waiting_qr_text = State()
    waiting_delay_input = State()
    waiting_rag_search = State()
    waiting_llm_test = State()
    waiting_meeting_kw = State()
    waiting_owner_id = State()
    waiting_calendar_event = State()
    waiting_repl_dir = State()
    waiting_repl_interval = State()
    waiting_temps = State()
    waiting_settle = State()
    waiting_history_limit = State()


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
            [_btn("⚙️ Настройки", "m:settings"), _btn("📈 Статистика", "m:stats")],
            [_btn("🧠 Обучение", "m:ai"), _btn("🎭 Стиль", "m:style")],
            [_btn("💾 Данные", "m:data"), _btn("📅 Календарь", "m:calendar")],
            [_btn("⚡ Заготовки", "m:quick"), _btn("🖥 Ресурсы", "m:resources")],
            [_btn("⏸ Пауза", "m:pause"), _btn("▶️ Резюме", "m:resume")],
            [_btn("❓ Помощь", "m:help")],
        ]
    )


def ai_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("🧠 Обучение моделей", "tr:status")],
            [_btn("🖥 Llama-server", "ls:status")],
            [_btn("🤖 LLM-модель", "llm:models")],
            [_btn("🎤 Whisper (голос)", "wh:menu")],
            [_btn("← Назад", "m:main")],
        ]
    )


def style_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("🌐 Глобальный профиль", "sty:global")],
            [_btn("👥 Персоны по чатам", "sty:personas")],
            [_btn("← Назад", "m:main")],
        ]
    )


def data_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("💾 Репликация БД", "rep:status")],
            [_btn("🗂 Бэкап диалогов", "dlg:chats")],
            [_btn("🔍 RAG-память", "rag:menu")],
            [_btn("← Назад", "m:main")],
        ]
    )


def _back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[_btn("← Назад", "m:main")]]
    )


def _onoff(value: bool) -> str:
    return "ВКЛ" if value else "ВЫКЛ"


async def settings_menu_kb(session) -> InlineKeyboardMarkup:
    auto = (
        await get_setting(session, "auto_reply", "1" if settings.auto_reply else "0")
    ) in _TRUE
    quality = (
        await get_setting(session, "quality_filter_enabled", "1")
    ) in _TRUE
    sched = await get_schedule_settings(session)
    delay = await get_delay_settings(session)
    persona_mode = await get_setting(session, "persona_mode", "global")
    group_mode = await get_setting(session, "group_reply_mode", "mention")
    reconcile = (
        await get_setting(session, "auto_reconcile_queue", "1")
    ) in _TRUE
    summary = (await get_setting(session, "summary_enabled", "1")) in _TRUE
    sched_label = (
        f"{sched.start[:2]}-{sched.end[:2]}" if sched.enabled else "ВЫКЛ"
    )
    persona_label = "по чатам" if persona_mode == "per_chat" else "глобальный"
    group_label = "все" if group_mode == "all" else "по @"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn(f"🔄 Авто-ответ: {_onoff(auto)}", "set:autoreply")],
            [_btn(f"⏰ Расписание: {sched_label}", "set:schedule")],
            [_btn(f"🎯 Фильтр качества: {_onoff(quality)}", "set:quality")],
            [_btn(f"⏱ Задержка: {_onoff(delay.enabled)}", "set:delay")],
            [_btn(f"🎭 Режим персоны: {persona_label}", "set:persona")],
            [_btn(f"👥 Ответ в группах: {group_label}", "set:groupreply")],
            [_btn(f"🧹 Авто-очистка очереди: {_onoff(reconcile)}", "set:reconcile")],
            [_btn(f"📝 Саммари диалогов: {_onoff(summary)}", "set:summary")],
            [
                _btn("🌡 Температуры", "set:temps"),
                _btn("⏱ Задержка мин/макс", "set:delaytime"),
            ],
            [
                _btn("⏲ Сборка ответа", "set:settle"),
                _btn("📚 Глубина истории", "set:history"),
            ],
            [
                _btn("🔑 Слова-встречи", "mk:menu"),
                _btn("🔔 Уведомления", "adm:menu"),
            ],
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
        "Всё управление доступно через меню /start — кнопки ниже\n"
        "дублируют веб-панель целиком.\n\n"
        "<b>Команды:</b>\n"
        "/start — главное меню\n"
        "/status — статус системы\n"
        "/pause /resume — пауза / возобновление\n"
        "/autoreply on|off — авто-ответ\n"
        "/schedule on 9 23 | off — расписание\n"
        "/stats [today|week|month] — статистика\n"
        "/pending — очередь сообщений\n"
        "/reply &lt;id&gt; &lt;текст&gt; — ручной ответ\n"
        "/model status|load|unload — модели\n"
        "/whitelist add|remove|list &lt;chat_id&gt; — чаты\n"
        "/calendar — календарь\n"
        "/training — обучение моделей\n"
        "/replication — репликация БД\n"
        "/rag — RAG-память\n"
        "/dialogs — бэкап диалогов\n"
        "/quick — быстрые ответы\n"
        "/style — профиль стиля\n"
        "/help — эта справка\n\n"
        "<b>Разделы меню</b>\n"
        "📊 Статус · 📋 Очередь · ⚙️ Настройки\n"
        "📈 Статистика · 🧠 Обучение · 🎭 Стиль\n"
        "💾 Данные · 📅 Календарь · ⚡ Заготовки · 🖥 Ресурсы"
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
            [_btn("➕ Создать событие", "cal:create")],
            [
                _btn("🔌 Проверить связь", "cal:test"),
                _btn("⚙️ Настройки", "cal:cfg"),
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
        err = status.get("error") or ""
        return (
            "📅 <b>Календарь</b>\n"
            "──────────────\n"
            "Статус: 🔴 не подключён\n"
            + (f"Ошибка: {_esc(str(err))}\n" if err else "")
            + "Учётные данные CalDAV задаются в .env "
            "(CALDAV_URL / CALDAV_USERNAME / CALDAV_PASSWORD).\n"
            "Нажми «🔌 Проверить связь», чтобы переподключиться."
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


async def _handle_calendar(
    cq: CallbackQuery, parts: list[str], state: FSMContext
) -> None:
    from .calendar_engine import calendar_engine
    from datetime import datetime as _dt, timedelta as _td

    action = parts[1] if len(parts) > 1 else ""
    if action == "cfg":
        await _edit_or_send(
            cq, await build_calendar_cfg_text(), await _calendar_cfg_kb()
        )
        return
    if action in ("cfgtgl", "cfgadj"):
        await _handle_calendar_cfg(cq, parts)
        return
    if action == "test":
        await cq.message.answer("⏳ Проверяю подключение к календарю…")
        from .caldav_config import refresh_calendar_connection

        ok = await refresh_calendar_connection()
        await cq.message.answer(
            "✅ Календарь подключён" if ok
            else f"🔴 Не удалось подключиться: {_esc(calendar_engine.last_error or '—')}"
        )
        await _edit_or_send(cq, await build_calendar_text(), _calendar_menu_kb())
        return
    if action == "create":
        await state.set_state(AdminStates.waiting_calendar_event)
        await cq.message.answer(
            "➕ <b>Создать событие</b>\n"
            "Отправь одной строкой: "
            "<code>Название | ГГГГ-ММ-ДД ЧЧ:ММ | длительность_мин</code>\n"
            "Например: <code>Созвон с Аней | 2026-05-22 15:00 | 60</code>"
        )
        return
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
            await _handle_calendar(cq, parts, state)
        elif kind == "st":
            await _handle_stats(cq, parts)
        elif kind == "tr":
            await _handle_training(cq, parts, state)
        elif kind == "ls":
            await _handle_llama(cq, parts)
        elif kind == "llm":
            await _handle_llm(cq, parts, state)
        elif kind == "wh":
            await _handle_whisper(cq, parts)
        elif kind == "sty":
            await _handle_style(cq, parts)
        elif kind == "rep":
            await _handle_replication(cq, parts, state)
        elif kind == "dlg":
            await _handle_dialogs(cq, parts)
        elif kind == "rag":
            await _handle_rag(cq, parts, state)
        elif kind == "qr":
            await _handle_quick(cq, parts, state)
        elif kind == "mk":
            await _handle_meeting_kw(cq, parts, state)
        elif kind == "adm":
            await _handle_admin_settings(cq, parts, state)
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
        await _edit_or_send(cq, "📈 <b>Статистика</b>", _stats_menu_kb())
    elif action == "ai":
        await _edit_or_send(cq, "🧠 <b>Обучение и модели</b>", ai_menu_kb())
    elif action == "style":
        await _edit_or_send(cq, "🎭 <b>Профиль стиля</b>", style_menu_kb())
    elif action == "data":
        await _edit_or_send(cq, "💾 <b>Данные и память</b>", data_menu_kb())
    elif action == "quick":
        await _send_quick_replies(cq)
    elif action == "resources":
        await _edit_or_send(cq, await build_resources_text(), _back_kb())
    elif action == "calendar":
        await _edit_or_send(
            cq, await build_calendar_text(), _calendar_menu_kb()
        )
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
    if action == "temps":
        await state.set_state(AdminStates.waiting_temps)
        await cq.message.answer(
            "Введи 3 температуры ответов через пробел (0–2),\n"
            "например «0.7 0.85 1.0»:"
        )
        return
    if action == "delaytime":
        await state.set_state(AdminStates.waiting_delay_input)
        await cq.message.answer(
            "Введи минимум и максимум задержки в секундах (30–600),\n"
            "например «60 180»:"
        )
        return
    if action == "settle":
        await state.set_state(AdminStates.waiting_settle)
        await cq.message.answer(
            "Через сколько секунд тишины собирать ответ (0–120)?\n"
            "Введи число:"
        )
        return
    if action == "history":
        await state.set_state(AdminStates.waiting_history_limit)
        await cq.message.answer(
            "Сколько последних сообщений брать в контекст (2–80)?\n"
            "Введи число:"
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
        elif action == "persona":
            cur = await get_setting(session, "persona_mode", "global")
            await set_setting(
                session,
                "persona_mode",
                "global" if cur == "per_chat" else "per_chat",
            )
        elif action == "groupreply":
            cur = await get_setting(session, "group_reply_mode", "mention")
            await set_setting(
                session,
                "group_reply_mode",
                "mention" if cur == "all" else "all",
            )
        elif action == "reconcile":
            cur = (
                await get_setting(session, "auto_reconcile_queue", "1")
            ) in _TRUE
            await set_setting(
                session, "auto_reconcile_queue", "0" if cur else "1"
            )
        elif action == "summary":
            cur = (await get_setting(session, "summary_enabled", "1")) in _TRUE
            await set_setting(
                session, "summary_enabled", "0" if cur else "1"
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
    dp.message.register(on_qr_text, owner, AdminStates.waiting_qr_text)
    dp.message.register(on_delay_input, owner, AdminStates.waiting_delay_input)
    dp.message.register(on_rag_search, owner, AdminStates.waiting_rag_search)
    dp.message.register(on_llm_test, owner, AdminStates.waiting_llm_test)
    dp.message.register(on_meeting_kw, owner, AdminStates.waiting_meeting_kw)
    dp.message.register(on_owner_id, owner, AdminStates.waiting_owner_id)
    dp.message.register(
        on_calendar_event, owner, AdminStates.waiting_calendar_event
    )
    dp.message.register(on_repl_dir, owner, AdminStates.waiting_repl_dir)
    dp.message.register(
        on_repl_interval, owner, AdminStates.waiting_repl_interval
    )
    dp.message.register(on_temps_input, owner, AdminStates.waiting_temps)
    dp.message.register(on_settle_input, owner, AdminStates.waiting_settle)
    dp.message.register(
        on_history_limit_input, owner, AdminStates.waiting_history_limit
    )

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
    dp.message.register(cmd_training, owner, Command("training"))
    dp.message.register(cmd_replication, owner, Command("replication"))
    dp.message.register(cmd_rag, owner, Command("rag"))
    dp.message.register(cmd_dialogs, owner, Command("dialogs"))
    dp.message.register(cmd_quick, owner, Command("quick"))
    dp.message.register(cmd_style, owner, Command("style"))
    dp.message.register(cmd_settings, owner, Command("settings"))
    dp.message.register(cmd_menu, owner, Command("menu"))
    dp.message.register(cmd_help, owner, Command("help"))

    # Callback queries.
    dp.callback_query.register(on_callback, owner)

    log.info("admin panel handlers registered")


# ==========================================================================
# Extended panels — full parity with the web UI
# ==========================================================================
def _nav(rows: list[list[InlineKeyboardButton]], back: str = "m:main") -> InlineKeyboardMarkup:
    rows = [list(r) for r in rows]
    rows.append([_btn("← Назад", back)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _short_path(value) -> str:
    if not value:
        return "—"
    try:
        from pathlib import Path as _P

        return _P(str(value)).name
    except Exception:  # noqa: BLE001
        return str(value)


def _clip(text: str, limit: int = 3500) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _fmt_size(num_bytes) -> str:
    try:
        mb = float(num_bytes) / (1024 * 1024)
    except (TypeError, ValueError):
        return "—"
    if mb >= 1024:
        return f"{mb / 1024:.1f} ГБ"
    return f"{mb:.1f} МБ"


# --------------------------------------------------------------------------
# System resources
# --------------------------------------------------------------------------
async def build_resources_text() -> str:
    from .config import ROOT_DIR

    lines = ["🖥 <b>Ресурсы сервера</b>", "──────────────────"]
    try:
        import psutil

        vm = psutil.virtual_memory()
        lines.append(
            f"RAM: {vm.used / 1024 ** 3:.1f} / {vm.total / 1024 ** 3:.1f} ГБ "
            f"({int(vm.percent)}%)"
        )
        lines.append(
            f"CPU: {int(psutil.cpu_percent(interval=0.1))}% "
            f"({psutil.cpu_count() or 0} ядер)"
        )
        try:
            du = psutil.disk_usage(str(ROOT_DIR))
            lines.append(
                f"Диск: {du.used / 1024 ** 3:.1f} / {du.total / 1024 ** 3:.1f} ГБ "
                f"({int(du.percent)}%)"
            )
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        lines.append("psutil недоступен")
    try:
        import torch

        if torch.cuda.is_available():
            free_b, total_b = torch.cuda.mem_get_info()
            used_b = total_b - free_b
            pct = int(used_b / total_b * 100) if total_b else 0
            lines.append(
                f"VRAM: {used_b / 1024 ** 3:.1f} / {total_b / 1024 ** 3:.1f} ГБ "
                f"({pct}%)"
            )
    except Exception:  # noqa: BLE001
        pass

    lines.append("")
    lines.append("<b>Модели</b>")
    try:
        llama = await llama_server_status_safe()
        running = bool(llama.get("running"))
        lines.append(f"{'✅' if running else '🔴'} Llama-server: "
                     f"{'онлайн' if running else 'офлайн'}")
    except Exception:  # noqa: BLE001
        pass
    try:
        from .whisper_engine import whisper_engine

        lines.append(
            f"{'✅' if whisper_engine.is_loaded else '⏸'} Whisper: "
            f"{'загружен' if whisper_engine.is_loaded else 'выгружен'}"
        )
    except Exception:  # noqa: BLE001
        pass
    try:
        from .rag_engine import rag_engine

        lines.append(
            f"{'✅' if rag_engine.available else '🔴'} RAG-эмбеддинги: "
            f"{'доступны' if rag_engine.available else 'недоступны'}"
        )
    except Exception:  # noqa: BLE001
        pass
    return "\n".join(lines)


async def llama_server_status_safe() -> dict:
    from . import llama_server

    return await llama_server.status_async()


# --------------------------------------------------------------------------
# Statistics
# --------------------------------------------------------------------------
def _stats_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("📊 Обзор", "st:overview"), _btn("📈 Активность", "st:activity")],
            [_btn("💬 Топ чатов", "st:top"), _btn("⏱ Время ответа", "st:response")],
            [
                _btn("🎓 Качество моделей", "st:quality"),
                _btn("🎤 Голосовые", "st:voice"),
            ],
            [
                _btn("Сегодня", "st:today"),
                _btn("Неделя", "st:week"),
                _btn("Месяц", "st:month"),
            ],
            [_btn("← Назад", "m:main")],
        ]
    )


async def _handle_stats(cq: CallbackQuery, parts: list[str]) -> None:
    action = parts[1] if len(parts) > 1 else "overview"
    back = _nav([], "m:stats")
    if action in ("today", "week", "month"):
        await _edit_or_send(cq, await build_stats_text(action), back)
    elif action == "overview":
        await _edit_or_send(cq, await build_stats_overview(), back)
    elif action == "activity":
        await _edit_or_send(cq, await build_stats_activity(), back)
    elif action == "top":
        await _edit_or_send(cq, await build_stats_top_chats(), back)
    elif action == "response":
        await _edit_or_send(cq, await build_stats_response_time(), back)
    elif action == "quality":
        await _edit_or_send(cq, await build_stats_model_quality(), back)
    elif action == "voice":
        await _edit_or_send(cq, await build_stats_voice(), back)


async def build_stats_overview() -> str:
    async with SessionLocal() as session:
        received = (await session.execute(
            select(func.count(DbMessage.id)).where(DbMessage.is_mine == False)  # noqa: E712
        )).scalar() or 0
        sent = (await session.execute(
            select(func.count(DbMessage.id)).where(DbMessage.is_mine == True)  # noqa: E712
        )).scalar() or 0
        replied = (await session.execute(
            select(func.count(DbMessage.id)).where(
                DbMessage.is_mine == False, DbMessage.replied == True  # noqa: E712
            )
        )).scalar() or 0
        good = (await session.execute(
            select(func.count(TrainingPair.id)).where(TrainingPair.feedback == "good")
        )).scalar() or 0
        bad = (await session.execute(
            select(func.count(TrainingPair.id)).where(TrainingPair.feedback == "bad")
        )).scalar() or 0
    total_fb = good + bad
    rate = int(good / total_fb * 100) if total_fb else 0
    return (
        "📊 <b>Обзор за всё время</b>\n"
        "──────────────────\n"
        f"├ Получено: {received}\n"
        f"├ Отправлено: {sent}\n"
        f"├ Отвечено: {replied}\n"
        f"├ Одобрено вручную: {good}\n"
        f"├ Отклонено: {bad}\n"
        f"└ Доля одобрения: {rate}%"
    )


async def build_stats_activity() -> str:
    since = datetime.utcnow() - timedelta(days=7)
    day = func.strftime("%Y-%m-%d", DbMessage.timestamp).label("day")
    async with SessionLocal() as session:
        result = await session.execute(
            select(
                day,
                func.sum(func.cast(DbMessage.is_mine == False, Integer)).label("received"),  # noqa: E712
                func.sum(func.cast(DbMessage.is_mine == True, Integer)).label("sent"),  # noqa: E712
            )
            .where(DbMessage.timestamp >= since)
            .group_by(day)
            .order_by(day)
        )
        rows = {
            r.day: (int(r.received or 0), int(r.sent or 0)) for r in result.all()
        }
    lines = ["📈 <b>Активность за 7 дней</b>", "──────────────────"]
    today = datetime.utcnow().date()
    for i in range(6, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        rec, snt = rows.get(d, (0, 0))
        lines.append(f"{d}: ← {rec}  → {snt}")
    return "\n".join(lines)


async def build_stats_top_chats() -> str:
    async with SessionLocal() as session:
        result = await session.execute(
            select(
                DbMessage.chat_id,
                func.max(DbMessage.chat_name).label("chat_name"),
                func.count(DbMessage.id).label("count"),
            )
            .group_by(DbMessage.chat_id)
            .order_by(desc("count"))
            .limit(10)
        )
        rows = result.all()
    if not rows:
        return "💬 <b>Топ чатов</b>\n──────────────────\nНет данных"
    lines = ["💬 <b>Топ-10 чатов</b>", "──────────────────"]
    for i, r in enumerate(rows, 1):
        name = r.chat_name or f"chat {r.chat_id}"
        lines.append(f"{i}. {_esc(name)} — {r.count}")
    return "\n".join(lines)


async def build_stats_response_time() -> str:
    async with SessionLocal() as session:
        result = await session.execute(
            select(
                DbMessage.chat_id,
                DbMessage.chat_name,
                DbMessage.is_mine,
                DbMessage.timestamp,
            ).order_by(DbMessage.chat_id, DbMessage.timestamp)
        )
        rows = result.all()
    by_chat: dict[int, dict] = {}
    pending_ts: dict[int, datetime] = {}
    chat_names: dict[int, str] = {}
    for chat_id, chat_name, is_mine, ts in rows:
        if chat_name:
            chat_names[chat_id] = chat_name
        if not is_mine:
            pending_ts.setdefault(chat_id, ts)
        else:
            start = pending_ts.pop(chat_id, None)
            if start is not None and ts > start:
                entry = by_chat.setdefault(chat_id, {"total": 0.0, "count": 0})
                entry["total"] += (ts - start).total_seconds()
                entry["count"] += 1
    items = [
        (chat_id, agg["total"] / agg["count"], agg["count"])
        for chat_id, agg in by_chat.items()
        if agg["count"]
    ]
    items.sort(key=lambda x: x[1])
    if not items:
        return "⏱ <b>Время ответа</b>\n──────────────────\nНет данных"
    lines = ["⏱ <b>Среднее время ответа</b>", "──────────────────"]
    for chat_id, avg, count in items[:12]:
        name = chat_names.get(chat_id, f"chat {chat_id}")
        if avg < 90:
            human = f"{int(avg)} с"
        elif avg < 5400:
            human = f"{int(avg // 60)} мин"
        else:
            human = f"{avg / 3600:.1f} ч"
        lines.append(f"• {_esc(name)}: {human} ({count})")
    return "\n".join(lines)


async def build_stats_model_quality() -> str:
    async with SessionLocal() as session:
        runs_q = await session.execute(
            select(TrainingRun)
            .where(TrainingRun.finished_at.is_not(None))
            .order_by(TrainingRun.finished_at)
        )
        runs = list(runs_q.scalars().all())
        lines = ["🎓 <b>Качество по версиям</b>", "──────────────────"]
        if not runs:
            lines.append("Обученных моделей ещё нет")
            return "\n".join(lines)
        for i, run in enumerate(runs):
            start = run.finished_at
            end = runs[i + 1].finished_at if i + 1 < len(runs) else None
            cond = [TrainingPair.timestamp >= start]
            if end is not None:
                cond.append(TrainingPair.timestamp < end)
            good = (await session.execute(
                select(func.count(TrainingPair.id)).where(
                    *cond, TrainingPair.feedback == "good"
                )
            )).scalar() or 0
            bad = (await session.execute(
                select(func.count(TrainingPair.id)).where(
                    *cond, TrainingPair.feedback == "bad"
                )
            )).scalar() or 0
            total = good + bad
            rate = int(good / total * 100) if total else 0
            mark = "✅" if run.is_active else "•"
            lines.append(
                f"{mark} v{run.version}: 👍{good} 👎{bad} — {rate}%"
            )
    return "\n".join(lines)


async def build_stats_voice() -> str:
    async with SessionLocal() as session:
        total = (await session.execute(
            select(func.count(DbMessage.id)).where(DbMessage.is_voice == True)  # noqa: E712
        )).scalar() or 0
        transcribed = (await session.execute(
            select(func.count(DbMessage.id)).where(
                DbMessage.is_voice == True,  # noqa: E712
                DbMessage.transcription.is_not(None),
                DbMessage.transcription != "",
            )
        )).scalar() or 0
        low = (await session.execute(
            select(func.count(DbMessage.id)).where(
                DbMessage.is_voice == True,  # noqa: E712
                DbMessage.transcription_low_confidence == True,  # noqa: E712
            )
        )).scalar() or 0
        avg = (await session.execute(
            select(func.avg(DbMessage.transcription_confidence)).where(
                DbMessage.is_voice == True,  # noqa: E712
                DbMessage.transcription_confidence.is_not(None),
            )
        )).scalar() or 0.0
    return (
        "🎤 <b>Голосовые сообщения</b>\n"
        "──────────────────\n"
        f"├ Получено: {total}\n"
        f"├ Расшифровано: {transcribed}\n"
        f"├ Низкая уверенность: {low}\n"
        f"└ Средняя уверенность: {float(avg):.2f}"
    )


# --------------------------------------------------------------------------
# Training
# --------------------------------------------------------------------------
def _training_kb(running: bool) -> InlineKeyboardMarkup:
    rows = [[_btn("🔨 Собрать датасет", "tr:build")]]
    if running:
        rows.append([_btn("⏹ Отменить обучение", "tr:cancel")])
    else:
        rows.append([_btn("▶️ Запустить обучение", "tr:start")])
    rows.append([_btn("📜 История запусков", "tr:runs")])
    rows.append([_btn("🔄 Обновить", "tr:status")])
    return _nav(rows, "m:ai")


async def build_training_text() -> str:
    from .trainer import list_runs, training_state
    from .dataset_builder import dataset_stats

    async with SessionLocal() as session:
        stats = await dataset_stats(session)
        msg_total = (
            await session.execute(select(func.count(DbMessage.id)))
        ).scalar() or 0
    runs = await list_runs()
    last = runs[0] if runs else None
    active = next((r for r in runs if r.get("is_active")), None)
    lines = [
        "🧠 <b>Обучение моделей</b>",
        "──────────────────",
        f"Сообщений собрано: {msg_total}",
        f"Обучающих пар: {stats['total_pairs']}",
        f"Датасет: {_esc(_short_path(stats.get('latest_dataset')))}",
    ]
    if training_state.running:
        lines.append(f"⚙️ Идёт обучение (run #{training_state.current_run_id})")
    if last:
        lines.append(
            f"Последний запуск: v{last['version']} — {_esc(str(last['status']))}"
        )
    if active:
        lines.append(f"✅ Активный адаптер: v{active['version']}")
    else:
        lines.append("Активный адаптер: чистая база")
    return "\n".join(lines)


async def _handle_training(
    cq: CallbackQuery, parts: list[str], state: FSMContext
) -> None:
    from .trainer import (
        activate_adapter,
        cancel_training,
        deactivate_adapter,
        delete_training_run,
        get_training_run_error_log,
        list_runs,
        start_training,
        training_state,
    )
    from .dataset_builder import build_dataset_file

    action = parts[1] if len(parts) > 1 else "status"
    if action == "status":
        await _edit_or_send(
            cq, await build_training_text(), _training_kb(training_state.running)
        )
    elif action == "build":
        await cq.message.answer("⏳ Собираю датасет из обучающих пар…")
        async with SessionLocal() as session:
            res = await build_dataset_file(session)
        if not res.get("total_pairs"):
            await cq.message.answer("Нет обучающих пар — датасет не собран.")
        else:
            await cq.message.answer(
                f"✅ Датасет v{res.get('version')}: "
                f"{res['total_pairs']} пар\n{_esc(_short_path(res.get('path')))}"
            )
    elif action == "start":
        res = await start_training("auto")
        if res.get("started"):
            await cq.message.answer(
                f"▶️ Обучение запущено (run #{res.get('run_id')})"
            )
        else:
            await cq.message.answer(
                f"Не запущено: {_esc(str(res.get('reason') or 'неизвестно'))}"
            )
        await _edit_or_send(
            cq, await build_training_text(), _training_kb(training_state.running)
        )
    elif action == "cancel":
        ok = await cancel_training()
        await cq.message.answer(
            "⏹ Обучение отменено" if ok else "Сейчас нечего отменять"
        )
    elif action == "runs":
        runs = await list_runs()
        if not runs:
            await _edit_or_send(
                cq, "📜 Запусков обучения ещё не было", _nav([], "tr:status")
            )
            return
        lines = ["📜 <b>История обучения</b>", "──────────────────"]
        rows: list[list[InlineKeyboardButton]] = []
        for r in runs[:10]:
            mark = "✅" if r.get("is_active") else "•"
            lines.append(
                f"{mark} v{r['version']} — {_esc(str(r.get('status')))} · "
                f"пар: {r.get('pair_count') or 0}"
            )
            row = []
            if not r.get("is_active") and r.get("status") == "completed":
                row.append(_btn(f"▶ v{r['version']}", f"tr:activate:{r['id']}"))
            if r.get("is_active"):
                row.append(_btn(f"⏏ v{r['version']}", "tr:deactivate"))
            if r.get("status") == "failed":
                row.append(_btn(f"📄 v{r['version']}", f"tr:errlog:{r['id']}"))
            row.append(_btn("🗑", f"tr:delask:{r['id']}"))
            rows.append(row)
        await _edit_or_send(cq, "\n".join(lines), _nav(rows, "tr:status"))
    elif action == "activate" and len(parts) >= 3:
        ok = await activate_adapter(int(parts[2]))
        await cq.message.answer(
            "✅ Адаптер активирован" if ok else "Не удалось активировать"
        )
    elif action == "deactivate":
        ok = await deactivate_adapter()
        await cq.message.answer(
            "⏏ Адаптер отключён — работает чистая база" if ok else "Не удалось"
        )
    elif action == "delask" and len(parts) >= 3:
        await _edit_or_send(
            cq,
            "🗑 Удалить этот запуск обучения? Файлы адаптера будут стёрты.",
            InlineKeyboardMarkup(inline_keyboard=[[
                _btn("✅ Да, удалить", f"tr:delok:{parts[2]}"),
                _btn("← Отмена", "tr:runs"),
            ]]),
        )
    elif action == "delok" and len(parts) >= 3:
        ok, reason = await delete_training_run(int(parts[2]))
        await cq.message.answer(
            "🗑 Запуск удалён" if ok else f"Не удалось: {_esc(str(reason))}"
        )
    elif action == "errlog" and len(parts) >= 3:
        info = await get_training_run_error_log(int(parts[2]))
        tail = info.get("log") or info.get("tail") or info.get("error") or "Лог пуст"
        await cq.message.answer(
            f"📄 <b>Лог ошибки</b>\n<pre>{_esc(str(tail)[-3000:])}</pre>"
        )


# --------------------------------------------------------------------------
# Llama-server
# --------------------------------------------------------------------------
async def build_llama_text() -> str:
    from . import llama_server

    status = await llama_server.status_async()
    running = bool(status.get("running"))
    lines = [
        "🖥 <b>Llama-server</b>",
        "──────────────────",
        f"Статус: {'✅ онлайн' if running else '🔴 офлайн'}",
        f"Порт: {status.get('port')}",
        f"Модель: {_esc(_short_path(status.get('base_model')))}",
    ]
    if status.get("lora_path"):
        lines.append(f"LoRA: {_esc(_short_path(status.get('lora_path')))}")
    if status.get("starting"):
        lines.append("⏳ Запускается…")
    if status.get("stopping"):
        lines.append("⏳ Останавливается…")
    lines.append(
        f"Авто-возобновление: {_onoff(bool(status.get('auto_resume')))}"
    )
    if status.get("last_error"):
        lines.append(f"Ошибка: {_esc(str(status['last_error']))}")
    if not status.get("configured"):
        lines.append("\n⚠️ LLAMA_BASE_MODEL_GGUF не задан в .env")
    return "\n".join(lines)


async def _llama_kb() -> InlineKeyboardMarkup:
    from . import llama_server

    status = await llama_server.status_async()
    running = bool(status.get("running"))
    rows = []
    if running:
        rows.append([_btn("⏹ Остановить", "ls:stop"), _btn("🔄 Рестарт", "ls:restart")])
    else:
        rows.append([_btn("▶️ Запустить", "ls:start")])
    rows.append([_btn(
        f"♻️ Авто-возобновление: {_onoff(bool(status.get('auto_resume')))}",
        "ls:autoresume",
    )])
    rows.append([_btn("🔄 Обновить", "ls:status")])
    return _nav(rows, "m:ai")


async def _handle_llama(cq: CallbackQuery, parts: list[str]) -> None:
    from . import llama_server

    action = parts[1] if len(parts) > 1 else "status"
    if action == "status":
        await _edit_or_send(cq, await build_llama_text(), await _llama_kb())
    elif action == "start":
        await cq.message.answer("⏳ Запускаю llama-server…")
        res = await llama_server.start()
        await cq.message.answer(
            "✅ Запущен" if res.get("started")
            else f"Не удалось: {_esc(str(res.get('reason') or '—'))}"
        )
        await _edit_or_send(cq, await build_llama_text(), await _llama_kb())
    elif action == "stop":
        res = await llama_server.stop()
        await cq.message.answer(
            "⏹ Остановлен" if res.get("stopped")
            else f"Не удалось: {_esc(str(res.get('reason') or '—'))}"
        )
        await _edit_or_send(cq, await build_llama_text(), await _llama_kb())
    elif action == "restart":
        await cq.message.answer("🔄 Рестарт запущен в фоне — следи через «Обновить».")
        asyncio.create_task(llama_server.restart())
    elif action == "autoresume":
        cur = await llama_server.get_auto_resume()
        await llama_server.set_auto_resume(not cur)
        await _edit_or_send(cq, await build_llama_text(), await _llama_kb())


# --------------------------------------------------------------------------
# LLM model
# --------------------------------------------------------------------------
async def _handle_llm(
    cq: CallbackQuery, parts: list[str], state: FSMContext
) -> None:
    from .llm_engine import get_client

    action = parts[1] if len(parts) > 1 else "models"
    if action == "models":
        client = get_client()
        try:
            models = await client.list_models()
        except Exception:  # noqa: BLE001
            models = []
        lines = [
            "🤖 <b>LLM-модель</b>",
            "──────────────────",
            f"Текущая: {_esc(client.model)}",
        ]
        rows: list[list[InlineKeyboardButton]] = []
        if models:
            lines.append(f"Доступно моделей: {len(models)}")
            for m in models[:12]:
                mark = "✅ " if m == client.model else ""
                rows.append([_btn(f"{mark}{m[:48]}", f"llm:set:{m[:48]}")])
        else:
            lines.append("Список моделей недоступен (сервер офлайн).")
        rows.append([_btn("🧪 Тест генерации", "llm:test")])
        await _edit_or_send(cq, "\n".join(lines), _nav(rows, "m:ai"))
    elif action == "set" and len(parts) >= 3:
        model = ":".join(parts[2:])
        async with SessionLocal() as session:
            await set_setting(session, "llm_model", model)
        get_client().model = model
        await cq.message.answer(f"✅ Модель переключена: {_esc(model)}")
        await _handle_llm(cq, ["llm", "models"], state)
    elif action == "test":
        await state.set_state(AdminStates.waiting_llm_test)
        await cq.message.answer("🧪 Напиши текст для теста генерации:")


# --------------------------------------------------------------------------
# Whisper settings
# --------------------------------------------------------------------------
WHISPER_MODELS = ["tiny", "base", "small", "medium", "large-v3"]
VOICE_MODES = ["text", "skip", "pending"]
VOICE_MODE_LABELS = {
    "text": "расшифровать в текст",
    "skip": "пропускать",
    "pending": "в очередь",
}


async def build_whisper_text() -> str:
    from .whisper_engine import whisper_engine

    async with SessionLocal() as session:
        enabled = (await get_setting(session, "whisper_enabled", "1")) in _TRUE
        model = await get_setting(session, "whisper_model", "large-v3")
        lang = await get_setting(session, "whisper_language", "ru")
        mode = await get_setting(session, "voice_reply_mode", "text")
        lazy = (await get_setting(session, "whisper_lazy_load", "0")) in _TRUE
    lines = [
        "🎤 <b>Whisper (распознавание речи)</b>",
        "──────────────────",
        f"Распознавание голоса: {_onoff(enabled)}",
        f"Модель: {_esc(model)}",
        f"Язык: {_esc(lang)}",
        f"Режим голосовых: {VOICE_MODE_LABELS.get(mode, mode)}",
        f"Ленивая загрузка: {_onoff(lazy)}",
        f"Модель в памяти: {'да' if whisper_engine.is_loaded else 'нет'}",
        f"Устройство: {_esc((whisper_engine.device or '—').upper())}",
        f"ffmpeg: {'есть' if whisper_engine.ffmpeg_available() else 'нет'}",
    ]
    if whisper_engine.last_error:
        lines.append(f"Ошибка: {_esc(whisper_engine.last_error)}")
    return "\n".join(lines)


async def _whisper_kb() -> InlineKeyboardMarkup:
    from .whisper_engine import whisper_engine

    async with SessionLocal() as session:
        enabled = (await get_setting(session, "whisper_enabled", "1")) in _TRUE
        lazy = (await get_setting(session, "whisper_lazy_load", "0")) in _TRUE
    rows = [
        [_btn(f"🎤 Распознавание: {_onoff(enabled)}", "wh:enable")],
        [_btn("🔁 Сменить модель", "wh:model")],
        [_btn("🗣 Режим голосовых", "wh:mode")],
        [_btn(f"💤 Ленивая загрузка: {_onoff(lazy)}", "wh:lazy")],
    ]
    if whisper_engine.is_loaded:
        rows.append([_btn("📤 Выгрузить из памяти", "wh:unload")])
    else:
        rows.append([_btn("📥 Загрузить в память", "wh:load")])
    rows.append([_btn("🔄 Обновить", "wh:menu")])
    return _nav(rows, "m:ai")


async def _handle_whisper(cq: CallbackQuery, parts: list[str]) -> None:
    from .whisper_engine import whisper_engine

    action = parts[1] if len(parts) > 1 else "menu"
    if action == "menu":
        await _edit_or_send(cq, await build_whisper_text(), await _whisper_kb())
        return
    async with SessionLocal() as session:
        if action == "enable":
            cur = (await get_setting(session, "whisper_enabled", "1")) in _TRUE
            await set_setting(session, "whisper_enabled", "0" if cur else "1")
        elif action == "lazy":
            cur = (await get_setting(session, "whisper_lazy_load", "0")) in _TRUE
            await set_setting(session, "whisper_lazy_load", "0" if cur else "1")
        elif action == "model":
            cur = await get_setting(session, "whisper_model", "large-v3")
            idx = WHISPER_MODELS.index(cur) if cur in WHISPER_MODELS else -1
            nxt = WHISPER_MODELS[(idx + 1) % len(WHISPER_MODELS)]
            await set_setting(session, "whisper_model", nxt)
        elif action == "mode":
            cur = await get_setting(session, "voice_reply_mode", "text")
            idx = VOICE_MODES.index(cur) if cur in VOICE_MODES else -1
            nxt = VOICE_MODES[(idx + 1) % len(VOICE_MODES)]
            await set_setting(session, "voice_reply_mode", nxt)
        elif action == "unload":
            whisper_engine.unload()
        elif action == "load":
            model_name = await get_setting(session, "whisper_model", "large-v3")
    if action == "load":
        await cq.message.answer("⏳ Загружаю модель Whisper…")
        loaded = whisper_engine.load(model_name)
        await cq.message.answer(
            "📥 Whisper загружен" if loaded else "Не удалось загрузить"
        )
    await _edit_or_send(cq, await build_whisper_text(), await _whisper_kb())


# --------------------------------------------------------------------------
# Style profile & personas
# --------------------------------------------------------------------------
_PROFILE_LABELS = {
    "tone": "Тон",
    "punctuation_style": "Пунктуация",
    "avg_message_length": "Средняя длина",
    "emoji_frequency": "Частота эмодзи",
    "uses_emoji": "Эмодзи",
    "uses_lowercase": "Нижний регистр",
    "avg_response_delay_minutes": "Задержка ответа, мин",
}


def _render_profile(profile: dict) -> str:
    if not profile:
        return "Профиль ещё не построен."
    lines = []
    for key, label in _PROFILE_LABELS.items():
        if key in profile:
            val = profile[key]
            if isinstance(val, bool):
                val = "да" if val else "нет"
            lines.append(f"├ {label}: {_esc(str(val))}")
    for key in ("greeting_patterns", "farewell_patterns", "common_words"):
        vals = profile.get(key) or []
        if vals:
            label = {
                "greeting_patterns": "Приветствия",
                "farewell_patterns": "Прощания",
                "common_words": "Частые слова",
            }[key]
            lines.append(f"├ {label}: {_esc(', '.join(map(str, vals[:8])))}")
    return "\n".join(lines) if lines else "Профиль пуст."


async def _handle_style(cq: CallbackQuery, parts: list[str]) -> None:
    from .style_engine import (
        get_latest_profile,
        get_profile_for_chat,
        list_chat_personas,
        reanalyze_and_store,
        reanalyze_chat_persona,
    )

    action = parts[1] if len(parts) > 1 else "global"
    if action == "global":
        async with SessionLocal() as session:
            profile = await get_latest_profile(session)
        text = (
            "🌐 <b>Глобальный профиль стиля</b>\n"
            "──────────────────\n" + _render_profile(profile or {})
        )
        kb = _nav([[_btn("🔁 Переанализировать", "sty:reanalyze")]], "m:style")
        await _edit_or_send(cq, text, kb)
    elif action == "reanalyze":
        await cq.message.answer("⏳ Анализирую стиль по сообщениям…")
        async with SessionLocal() as session:
            profile = await reanalyze_and_store(session)
        text = (
            "🌐 <b>Глобальный профиль обновлён</b>\n"
            "──────────────────\n" + _render_profile(profile or {})
        )
        kb = _nav([[_btn("🔁 Переанализировать", "sty:reanalyze")]], "m:style")
        await _edit_or_send(cq, text, kb)
    elif action == "personas":
        async with SessionLocal() as session:
            personas = await list_chat_personas(session)
        if not personas:
            await _edit_or_send(
                cq,
                "👥 <b>Персоны по чатам</b>\n──────────────────\n"
                "Персоны ещё не построены. Включи режим «по чатам» "
                "в настройках, чтобы они создавались автоматически.",
                _nav([], "m:style"),
            )
            return
        lines = ["👥 <b>Персоны по чатам</b>", "──────────────────"]
        rows: list[list[InlineKeyboardButton]] = []
        for p in personas[:20]:
            name = p.get("chat_name") or f"chat {p['chat_id']}"
            lines.append(f"• {_esc(name)} ({p.get('messages_count', 0)} сообщ.)")
            rows.append([_btn(name[:48], f"sty:view:{p['chat_id']}")])
        await _edit_or_send(cq, "\n".join(lines), _nav(rows, "m:style"))
    elif action == "view" and len(parts) >= 3:
        chat_id = int(parts[2])
        async with SessionLocal() as session:
            profile = await get_profile_for_chat(session, chat_id)
        text = (
            f"🎭 <b>Персона чата {chat_id}</b>\n"
            "──────────────────\n" + _render_profile(profile or {})
        )
        kb = _nav(
            [
                [_btn("🔁 Переанализировать", f"sty:re:{chat_id}")],
                [_btn("🗑 Удалить персону", f"sty:delask:{chat_id}")],
            ],
            "sty:personas",
        )
        await _edit_or_send(cq, text, kb)
    elif action == "re" and len(parts) >= 3:
        chat_id = int(parts[2])
        await cq.message.answer("⏳ Переанализирую персону…")
        async with SessionLocal() as session:
            await reanalyze_chat_persona(session, chat_id)
        await _handle_style(cq, ["sty", "view", str(chat_id)])
    elif action == "delask" and len(parts) >= 3:
        await _edit_or_send(
            cq,
            "🗑 Удалить персону этого чата?",
            InlineKeyboardMarkup(inline_keyboard=[[
                _btn("✅ Да", f"sty:delok:{parts[2]}"),
                _btn("← Отмена", "sty:personas"),
            ]]),
        )
    elif action == "delok" and len(parts) >= 3:
        chat_id = int(parts[2])
        async with SessionLocal() as session:
            row = (await session.execute(
                select(ChatPersona).where(ChatPersona.chat_id == chat_id)
            )).scalar_one_or_none()
            if row:
                await session.delete(row)
                await session.commit()
        await cq.message.answer("🗑 Персона удалена")
        await _handle_style(cq, ["sty", "personas"])


# --------------------------------------------------------------------------
# Replication
# --------------------------------------------------------------------------
async def build_replication_text() -> str:
    from .replication import status as repl_status

    st = await repl_status()
    cfg = st.get("settings", {})
    last = st.get("last_run")
    lines = [
        "💾 <b>Репликация БД</b>",
        "──────────────────",
        f"Автоматически: {_onoff(cfg.get('enabled'))}",
        f"Интервал: {cfg.get('interval_minutes')} мин",
        f"Хранить копий: {cfg.get('retention')}",
        f"Папка: {_esc(str(cfg.get('target_dir') or '—'))}",
        f"Размер БД: {_fmt_size(st.get('source_bytes'))}",
        f"Идёт сейчас: {'да' if st.get('running') else 'нет'}",
    ]
    if last:
        lines.append(
            f"Последняя копия: {_esc(str(last.get('status')))} · "
            f"{_fmt_size(last.get('copied_bytes'))}"
        )
        if last.get("error"):
            lines.append(f"Ошибка: {_esc(str(last['error']))}")
    return "\n".join(lines)


async def _replication_kb() -> InlineKeyboardMarkup:
    from .replication import status as repl_status

    st = await repl_status()
    cfg = st.get("settings", {})
    rows = []
    if st.get("running"):
        rows.append([_btn("⏹ Отменить", "rep:cancel")])
    else:
        rows.append([_btn("▶️ Сделать копию сейчас", "rep:run")])
    rows.append([_btn(
        f"♻️ Авто-репликация: {_onoff(cfg.get('enabled'))}", "rep:toggle"
    )])
    rows.append([
        _btn("⏱ Интервал", "rep:setint"),
        _btn("📁 Папка", "rep:setdir"),
    ])
    rows.append([_btn("📜 История копий", "rep:runs")])
    rows.append([_btn("🔄 Обновить", "rep:status")])
    return _nav(rows, "m:data")


async def _handle_replication(
    cq: CallbackQuery, parts: list[str], state: FSMContext
) -> None:
    from .replication import (
        cancel_replication,
        delete_run,
        get_run_log,
        list_runs as repl_list_runs,
        run_replication,
        save_settings as save_repl_settings,
        status as repl_status,
    )

    action = parts[1] if len(parts) > 1 else "status"
    if action == "status":
        await _edit_or_send(
            cq, await build_replication_text(), await _replication_kb()
        )
    elif action == "run":
        res = await run_replication(trigger="manual")
        if res.get("started"):
            await cq.message.answer("▶️ Репликация запущена")
        else:
            await cq.message.answer(
                f"Не запущена: {_esc(str(res.get('reason') or '—'))}"
            )
        await _edit_or_send(
            cq, await build_replication_text(), await _replication_kb()
        )
    elif action == "cancel":
        ok = await cancel_replication()
        await cq.message.answer("⏹ Отменено" if ok else "Нечего отменять")
    elif action == "toggle":
        st = await repl_status()
        cur = bool(st.get("settings", {}).get("enabled"))
        async with SessionLocal() as session:
            await save_repl_settings(session, enabled=not cur)
        await _edit_or_send(
            cq, await build_replication_text(), await _replication_kb()
        )
    elif action == "setint":
        await state.set_state(AdminStates.waiting_repl_interval)
        await cq.message.answer(
            "Введи интервал репликации в минутах (1–10080):"
        )
    elif action == "setdir":
        await state.set_state(AdminStates.waiting_repl_dir)
        await cq.message.answer("Введи путь к папке для копий БД:")
    elif action == "runs":
        runs = await repl_list_runs()
        if not runs:
            await _edit_or_send(
                cq, "📜 Копий ещё не было", _nav([], "rep:status")
            )
            return
        lines = ["📜 <b>История репликаций</b>", "──────────────────"]
        rows: list[list[InlineKeyboardButton]] = []
        for r in runs[:10]:
            lines.append(
                f"#{r['id']} · {_esc(str(r.get('status')))} · "
                f"{_fmt_size(r.get('copied_bytes'))}"
            )
            row = [_btn(f"📄 #{r['id']}", f"rep:log:{r['id']}")]
            if r.get("exists"):
                row.append(_btn("🗑", f"rep:delask:{r['id']}"))
            rows.append(row)
        await _edit_or_send(cq, "\n".join(lines), _nav(rows, "rep:status"))
    elif action == "log" and len(parts) >= 3:
        info = await get_run_log(int(parts[2]))
        body = info.get("log") or info.get("tail") or info.get("error") or "Пусто"
        await cq.message.answer(
            f"📄 <b>Лог репликации #{parts[2]}</b>\n"
            f"<pre>{_esc(str(body)[-3000:])}</pre>"
        )
    elif action == "delask" and len(parts) >= 3:
        await _edit_or_send(
            cq,
            "🗑 Удалить этот файл резервной копии?",
            InlineKeyboardMarkup(inline_keyboard=[[
                _btn("✅ Да", f"rep:delok:{parts[2]}"),
                _btn("← Отмена", "rep:runs"),
            ]]),
        )
    elif action == "delok" and len(parts) >= 3:
        ok, reason = await delete_run(int(parts[2]), confirm=True)
        await cq.message.answer(
            "🗑 Копия удалена" if ok else f"Не удалось: {_esc(str(reason))}"
        )


# --------------------------------------------------------------------------
# Dialog backups
# --------------------------------------------------------------------------
async def _handle_dialogs(cq: CallbackQuery, parts: list[str]) -> None:
    from .dialog_backup import (
        SETTING_INTERVAL,
        get_excluded_chats,
        get_interval_minutes,
        scheduler as dlg_scheduler,
        set_excluded_chats,
    )

    action = parts[1] if len(parts) > 1 else "chats"
    if action == "chats":
        async with SessionLocal() as session:
            result = await session.execute(
                select(
                    DbMessage.chat_id,
                    func.max(DbMessage.chat_name).label("chat_name"),
                    func.count(DbMessage.id).label("count"),
                )
                .where(DbMessage.deleted == False)  # noqa: E712
                .group_by(DbMessage.chat_id)
                .order_by(desc("count"))
                .limit(20)
            )
            chats = result.all()
            backups = await session.execute(
                select(
                    DialogBackup.chat_id,
                    func.count(DialogBackup.id).label("versions"),
                ).group_by(DialogBackup.chat_id)
            )
            ver_by_chat = {r.chat_id: r.versions for r in backups.all()}
            excluded = await get_excluded_chats(session)
        lines = ["🗂 <b>Бэкап диалогов</b>", "──────────────────"]
        rows: list[list[InlineKeyboardButton]] = [
            [_btn("▶️ Сделать бэкап сейчас", "dlg:runbackup")],
            [_btn("⚙️ Настройки бэкапа", "dlg:settings")],
        ]
        for c in chats:
            name = c.chat_name or f"chat {c.chat_id}"
            v = ver_by_chat.get(c.chat_id, 0)
            excl = "🚫" if c.chat_id in excluded else ""
            lines.append(
                f"{excl}• {_esc(name)} — {c.count} сообщ., версий: {v}"
            )
            rows.append([_btn(f"{excl}{name[:40]} (v{v})",
                              f"dlg:versions:{c.chat_id}")])
        await _edit_or_send(cq, _clip("\n".join(lines)), _nav(rows, "m:data"))
    elif action == "runbackup":
        await cq.message.answer("⏳ Делаю бэкап диалогов…")
        res = await dlg_scheduler.run_now()
        await cq.message.answer(
            "✅ Бэкап готов\n"
            f"├ Чатов обработано: {res.chats_processed}\n"
            f"├ Новых версий: {res.new_versions}\n"
            f"├ Без изменений: {res.skipped}\n"
            f"└ Исключено: {res.excluded}"
        )
    elif action == "settings":
        async with SessionLocal() as session:
            interval = await get_interval_minutes(session)
        text = (
            "⚙️ <b>Настройки бэкапа диалогов</b>\n"
            "──────────────────\n"
            f"Интервал: {interval} мин ({interval // 60} ч)\n"
        )
        if dlg_scheduler.last_error:
            text += f"Последняя ошибка: {_esc(dlg_scheduler.last_error)}\n"
        rows = [
            [
                _btn("6 ч", "dlg:int:360"),
                _btn("12 ч", "dlg:int:720"),
                _btn("24 ч", "dlg:int:1440"),
                _btn("48 ч", "dlg:int:2880"),
            ],
        ]
        await _edit_or_send(cq, text, _nav(rows, "dlg:chats"))
    elif action == "int" and len(parts) >= 3:
        async with SessionLocal() as session:
            await set_setting(session, SETTING_INTERVAL, str(int(parts[2])))
        dlg_scheduler.trigger()
        await cq.message.answer(f"✅ Интервал бэкапа: {int(parts[2]) // 60} ч")
        await _handle_dialogs(cq, ["dlg", "settings"])
    elif action == "versions" and len(parts) >= 3:
        chat_id = int(parts[2])
        async with SessionLocal() as session:
            rows_db = (await session.execute(
                select(DialogBackup)
                .where(DialogBackup.chat_id == chat_id)
                .order_by(desc(DialogBackup.version))
            )).scalars().all()
            excluded = await get_excluded_chats(session)
        excl = chat_id in excluded
        lines = [f"🗂 <b>Версии бэкапа · чат {chat_id}</b>", "──────────────────"]
        rows: list[list[InlineKeyboardButton]] = [[
            _btn(
                "✅ В бэкапе" if not excl else "🚫 Исключён",
                f"dlg:exc:{chat_id}",
            )
        ]]
        if not rows_db:
            lines.append("Версий пока нет.")
        for b in rows_db[:15]:
            lines.append(
                f"v{b.version} — {b.message_count} сообщ. "
                f"({_fmt_local(b.created_at, 'Europe/Moscow', '%d.%m %H:%M')})"
            )
            rows.append([_btn(f"📄 Скачать v{b.version}", f"dlg:get:{b.id}")])
        await _edit_or_send(cq, "\n".join(lines), _nav(rows, "dlg:chats"))
    elif action == "exc" and len(parts) >= 3:
        chat_id = int(parts[2])
        async with SessionLocal() as session:
            excluded = await get_excluded_chats(session)
            if chat_id in excluded:
                excluded.discard(chat_id)
            else:
                excluded.add(chat_id)
            await set_excluded_chats(session, list(excluded))
        await _handle_dialogs(cq, ["dlg", "versions", str(chat_id)])
    elif action == "get" and len(parts) >= 3:
        await _send_backup_document(cq, int(parts[2]))


async def _send_backup_document(cq: CallbackQuery, backup_id: int) -> None:
    from aiogram.types import BufferedInputFile

    async with SessionLocal() as session:
        backup = (await session.execute(
            select(DialogBackup).where(DialogBackup.id == backup_id)
        )).scalar_one_or_none()
        if not backup:
            await cq.message.answer("Бэкап не найден")
            return
        msgs = (await session.execute(
            select(DialogBackupMessage)
            .where(DialogBackupMessage.backup_id == backup_id)
            .order_by(DialogBackupMessage.timestamp, DialogBackupMessage.id)
        )).scalars().all()
    chat_title = backup.chat_name or f"chat {backup.chat_id}"
    out = [
        f"Диалог: {chat_title}",
        f"chat_id: {backup.chat_id}",
        f"Версия: v{backup.version}",
        f"Сообщений: {backup.message_count}",
        "=" * 40,
        "",
    ]
    for m in msgs:
        author = settings.display_name if m.is_mine else (m.sender_name or "собеседник")
        out.append(f"[{m.timestamp}] {author}:")
        out.append((m.text or "").strip())
        out.append("")
    data = "\n".join(out).encode("utf-8")
    fname = f"dialog_{backup.chat_id}_v{backup.version}.txt"
    await cq.message.answer_document(
        BufferedInputFile(data, filename=fname),
        caption=f"🗂 {chat_title} · v{backup.version}",
    )


# --------------------------------------------------------------------------
# RAG memory
# --------------------------------------------------------------------------
async def build_rag_text() -> str:
    from .rag_engine import EMBED_MODEL_NAME, rag_engine

    async with SessionLocal() as session:
        enabled = (await get_setting(session, "rag_enabled", "1")) in _TRUE
        cross = (await get_setting(session, "rag_search_cross_chat", "1")) in _TRUE
    stats = await rag_engine.get_stats()
    progress = rag_engine.progress or {}
    lines = [
        "🔍 <b>RAG-память</b>",
        "──────────────────",
        f"Включено: {_onoff(enabled)}",
        f"Доступно: {'да' if rag_engine.available else 'нет'}",
        f"Проиндексировано: {stats.total_indexed}",
        f"Размер базы: {stats.collection_size_mb:.1f} МБ",
        f"Поиск по всем чатам: {_onoff(cross)}",
        f"Модель: {_esc(EMBED_MODEL_NAME)}",
        f"Устройство: {_esc((rag_engine.device or '—').upper())}",
    ]
    if progress.get("running"):
        lines.append(
            f"⚙️ Индексация: {progress.get('indexed')}/{progress.get('total')} "
            f"({progress.get('percent')}%)"
        )
    if rag_engine.last_error:
        lines.append(f"Ошибка: {_esc(rag_engine.last_error)}")
    return "\n".join(lines)


async def _rag_kb() -> InlineKeyboardMarkup:
    async with SessionLocal() as session:
        enabled = (await get_setting(session, "rag_enabled", "1")) in _TRUE
        cross = (await get_setting(session, "rag_search_cross_chat", "1")) in _TRUE
    return _nav(
        [
            [_btn(f"🔍 RAG: {_onoff(enabled)}", "rag:toggle")],
            [_btn(f"🌐 Поиск по всем чатам: {_onoff(cross)}", "rag:crosschat")],
            [_btn("📥 Переиндексировать всё", "rag:index")],
            [_btn("🔎 Поиск по памяти", "rag:search")],
            [_btn("🧹 Очистить индекс", "rag:clearask")],
            [_btn("🔄 Обновить", "rag:menu")],
        ],
        "m:data",
    )


async def _handle_rag(
    cq: CallbackQuery, parts: list[str], state: FSMContext
) -> None:
    from .rag_engine import rag_engine

    action = parts[1] if len(parts) > 1 else "menu"
    if action in ("menu", "status"):
        await _edit_or_send(cq, await build_rag_text(), await _rag_kb())
    elif action == "toggle":
        async with SessionLocal() as session:
            cur = (await get_setting(session, "rag_enabled", "1")) in _TRUE
            await set_setting(session, "rag_enabled", "0" if cur else "1")
        await _edit_or_send(cq, await build_rag_text(), await _rag_kb())
    elif action == "crosschat":
        async with SessionLocal() as session:
            cur = (
                await get_setting(session, "rag_search_cross_chat", "1")
            ) in _TRUE
            await set_setting(
                session, "rag_search_cross_chat", "0" if cur else "1"
            )
        await _edit_or_send(cq, await build_rag_text(), await _rag_kb())
    elif action == "index":
        if (rag_engine.progress or {}).get("running"):
            await cq.message.answer("Индексация уже идёт.")
            return
        if not await rag_engine.initialize():
            await cq.message.answer(
                f"RAG недоступен: {_esc(rag_engine.last_error or '—')}"
            )
            return
        await cq.message.answer("📥 Запустил переиндексацию в фоне…")

        async def _run() -> None:
            try:
                async with SessionLocal() as session:
                    await rag_engine.index_all_messages(session)
            except Exception:  # noqa: BLE001
                log.exception("RAG index from bot failed")

        asyncio.create_task(_run())
    elif action == "search":
        await state.set_state(AdminStates.waiting_rag_search)
        await cq.message.answer("🔎 Введи поисковый запрос по истории:")
    elif action == "clearask":
        await _edit_or_send(
            cq,
            "🧹 Очистить весь RAG-индекс? Сообщения придётся индексировать заново.",
            InlineKeyboardMarkup(inline_keyboard=[[
                _btn("✅ Да, очистить", "rag:clearok"),
                _btn("← Отмена", "rag:menu"),
            ]]),
        )
    elif action == "clearok":
        await rag_engine.clear()
        await cq.message.answer("🧹 RAG-индекс очищен")
        await _edit_or_send(cq, await build_rag_text(), await _rag_kb())


# --------------------------------------------------------------------------
# Quick replies
# --------------------------------------------------------------------------
async def _quick_payload() -> tuple[str, InlineKeyboardMarkup]:
    async with SessionLocal() as session:
        rows = (await session.execute(
            select(QuickReply).order_by(
                desc(QuickReply.usage_count), desc(QuickReply.created_at)
            )
        )).scalars().all()
    if not rows:
        text = (
            "⚡ <b>Быстрые ответы</b>\n──────────────────\n"
            "Заготовок пока нет. Нажми «Добавить»."
        )
    else:
        lines = ["⚡ <b>Быстрые ответы</b>", "──────────────────"]
        for r in rows[:30]:
            lines.append(
                f"• [{_esc(r.category)}] {_esc(r.text)} — {r.usage_count}×"
            )
        text = _clip("\n".join(lines))
    kb_rows: list[list[InlineKeyboardButton]] = [[_btn("➕ Добавить", "qr:add")]]
    for r in rows[:20]:
        kb_rows.append([_btn(f"🗑 {r.text[:42]}", f"qr:delask:{r.id}")])
    return text, _nav(kb_rows, "m:main")


async def _send_quick_replies(cq: CallbackQuery) -> None:
    text, kb = await _quick_payload()
    await _edit_or_send(cq, text, kb)


async def _handle_quick(
    cq: CallbackQuery, parts: list[str], state: FSMContext
) -> None:
    action = parts[1] if len(parts) > 1 else "list"
    if action == "list":
        await _send_quick_replies(cq)
    elif action == "add":
        await state.set_state(AdminStates.waiting_qr_text)
        await cq.message.answer(
            "➕ Введи текст заготовки.\n"
            "Можно указать категорию: <code>категория | текст</code>"
        )
    elif action == "delask" and len(parts) >= 3:
        await _edit_or_send(
            cq,
            "🗑 Удалить эту заготовку?",
            InlineKeyboardMarkup(inline_keyboard=[[
                _btn("✅ Да", f"qr:delok:{parts[2]}"),
                _btn("← Отмена", "qr:list"),
            ]]),
        )
    elif action == "delok" and len(parts) >= 3:
        async with SessionLocal() as session:
            row = await session.get(QuickReply, int(parts[2]))
            if row:
                await session.delete(row)
                await session.commit()
        await _send_quick_replies(cq)


# --------------------------------------------------------------------------
# Meeting keywords
# --------------------------------------------------------------------------
async def _meeting_kw_payload() -> tuple[str, InlineKeyboardMarkup]:
    from . import meeting_detector

    async with SessionLocal() as session:
        raw = await get_setting(session, "meeting_phrases", "[]")
    try:
        user = [p for p in json.loads(raw) if isinstance(p, str)]
    except (ValueError, TypeError):
        user = []
    builtin = meeting_detector.get_builtin_keywords()
    lines = [
        "🔑 <b>Слова-триггеры встреч</b>",
        "──────────────────",
        f"Встроенных фраз: {len(builtin)}",
        "",
        "<b>Свои фразы:</b>",
    ]
    if user:
        for i, p in enumerate(user):
            lines.append(f"{i + 1}. {_esc(p)}")
    else:
        lines.append("(пока нет)")
    rows: list[list[InlineKeyboardButton]] = [[_btn("➕ Добавить фразу", "mk:add")]]
    for i, p in enumerate(user[:20]):
        rows.append([_btn(f"🗑 {p[:42]}", f"mk:del:{i}")])
    if user:
        rows.append([_btn("🧹 Удалить все свои", "mk:clear")])
    return "\n".join(lines), _nav(rows, "m:settings")


async def _handle_meeting_kw(
    cq: CallbackQuery, parts: list[str], state: FSMContext
) -> None:
    from . import meeting_detector

    action = parts[1] if len(parts) > 1 else "menu"
    if action == "menu":
        text, kb = await _meeting_kw_payload()
        await _edit_or_send(cq, text, kb)
    elif action == "add":
        await state.set_state(AdminStates.waiting_meeting_kw)
        await cq.message.answer("➕ Введи фразу-триггер встречи:")
    elif action == "del" and len(parts) >= 3:
        idx = int(parts[2])
        async with SessionLocal() as session:
            raw = await get_setting(session, "meeting_phrases", "[]")
            try:
                user = [p for p in json.loads(raw) if isinstance(p, str)]
            except (ValueError, TypeError):
                user = []
            if 0 <= idx < len(user):
                user.pop(idx)
            await set_setting(
                session, "meeting_phrases", json.dumps(user, ensure_ascii=False)
            )
        meeting_detector.set_user_keywords(user)
        text, kb = await _meeting_kw_payload()
        await _edit_or_send(cq, text, kb)
    elif action == "clear":
        async with SessionLocal() as session:
            await set_setting(session, "meeting_phrases", "[]")
        meeting_detector.set_user_keywords([])
        text, kb = await _meeting_kw_payload()
        await _edit_or_send(cq, text, kb)


# --------------------------------------------------------------------------
# Admin notification settings
# --------------------------------------------------------------------------
async def _admin_settings_payload() -> tuple[str, InlineKeyboardMarkup]:
    async with SessionLocal() as session:
        owner = await get_setting(session, "owner_chat_id", settings.owner_chat_id)
        notify_auto = (
            await get_setting(session, "admin_notify_auto", "1")
        ) in _TRUE
        notify_pending = (
            await get_setting(session, "admin_notify_pending", "1")
        ) in _TRUE
    text = (
        "🔔 <b>Уведомления админ-панели</b>\n"
        "──────────────────\n"
        f"Owner chat_id: {_esc(owner or '— не задан')}\n"
        f"Уведомлять об авто-ответах: {_onoff(notify_auto)}\n"
        f"Уведомлять о новых сообщениях: {_onoff(notify_pending)}"
    )
    kb = _nav(
        [
            [_btn(f"🤖 Авто-ответы: {_onoff(notify_auto)}", "adm:notifyauto")],
            [_btn(f"📨 Новые сообщения: {_onoff(notify_pending)}", "adm:notifypending")],
            [_btn("🆔 Задать owner chat_id", "adm:setowner")],
            [_btn("🔔 Тестовое уведомление", "adm:test")],
        ],
        "m:settings",
    )
    return text, kb


async def _handle_admin_settings(
    cq: CallbackQuery, parts: list[str], state: FSMContext
) -> None:
    action = parts[1] if len(parts) > 1 else "menu"
    if action == "menu":
        text, kb = await _admin_settings_payload()
        await _edit_or_send(cq, text, kb)
    elif action == "notifyauto":
        async with SessionLocal() as session:
            cur = (await get_setting(session, "admin_notify_auto", "1")) in _TRUE
            await set_setting(session, "admin_notify_auto", "0" if cur else "1")
        text, kb = await _admin_settings_payload()
        await _edit_or_send(cq, text, kb)
    elif action == "notifypending":
        async with SessionLocal() as session:
            cur = (
                await get_setting(session, "admin_notify_pending", "1")
            ) in _TRUE
            await set_setting(
                session, "admin_notify_pending", "0" if cur else "1"
            )
        text, kb = await _admin_settings_payload()
        await _edit_or_send(cq, text, kb)
    elif action == "setowner":
        await state.set_state(AdminStates.waiting_owner_id)
        await cq.message.answer(
            "🆔 Введи числовой owner chat_id "
            "(или напиши боту /start этим аккаунтом):"
        )
    elif action == "test":
        ok = await send_test_notification()
        await cq.message.answer(
            "🔔 Тестовое уведомление отправлено" if ok
            else "Не удалось отправить — проверь owner_chat_id"
        )


# --------------------------------------------------------------------------
# Calendar config
# --------------------------------------------------------------------------
async def build_calendar_cfg_text() -> str:
    from .caldav_config import get_caldav_config

    async with SessionLocal() as session:
        cfg = await get_caldav_config(session)
    return (
        "⚙️ <b>Настройки календаря</b>\n"
        "──────────────────\n"
        f"CalDAV URL: {_esc(cfg.get('caldav_url') or '—')}\n"
        f"Пользователь: {_esc(cfg.get('caldav_username') or '—')}\n"
        f"Пароль: {'задан' if cfg.get('caldav_password') else 'не задан'}\n"
        f"Рабочие часы: {cfg.get('caldav_work_start')}:00–"
        f"{cfg.get('caldav_work_end')}:00\n"
        f"Горизонт планирования: {cfg.get('caldav_lookahead_days')} дн.\n"
        f"Длина слота: {cfg.get('caldav_slot_duration')} мин\n\n"
        "Учётные данные (URL/логин/пароль) задаются в .env."
    )


async def _calendar_cfg_kb() -> InlineKeyboardMarkup:
    from .caldav_config import get_caldav_config

    async with SessionLocal() as session:
        cfg = await get_caldav_config(session)
    return _nav(
        [
            [_btn(f"📅 Интеграция: {_onoff(cfg.get('caldav_enabled'))}",
                  "cal:cfgtgl:caldav_enabled")],
            [_btn(f"🤖 Авто-создание встреч: {_onoff(cfg.get('caldav_auto_create'))}",
                  "cal:cfgtgl:caldav_auto_create")],
            [_btn(f"💡 Предлагать слоты: {_onoff(cfg.get('caldav_propose_slots'))}",
                  "cal:cfgtgl:caldav_propose_slots")],
            [_btn(f"🔔 Уведомлять о встречах: {_onoff(cfg.get('caldav_notify'))}",
                  "cal:cfgtgl:caldav_notify")],
            [
                _btn("Начало −", "cal:cfgadj:caldav_work_start:-1"),
                _btn("Начало +", "cal:cfgadj:caldav_work_start:1"),
            ],
            [
                _btn("Конец −", "cal:cfgadj:caldav_work_end:-1"),
                _btn("Конец +", "cal:cfgadj:caldav_work_end:1"),
            ],
            [
                _btn("Горизонт −", "cal:cfgadj:caldav_lookahead_days:-1"),
                _btn("Горизонт +", "cal:cfgadj:caldav_lookahead_days:1"),
            ],
        ],
        "m:calendar",
    )


async def _handle_calendar_cfg(cq: CallbackQuery, parts: list[str]) -> None:
    from .caldav_config import (
        get_caldav_config,
        refresh_calendar_connection,
        save_caldav_config,
    )

    kind = parts[1]
    async with SessionLocal() as session:
        cfg = await get_caldav_config(session)
        if kind == "cfgtgl" and len(parts) >= 3:
            key = parts[2]
            await save_caldav_config(session, {key: not cfg.get(key)})
        elif kind == "cfgadj" and len(parts) >= 4:
            key, delta = parts[2], int(parts[3])
            cur = int(cfg.get(key) or 0)
            new = cur + delta
            if key in ("caldav_work_start", "caldav_work_end"):
                new = max(0, min(23, new))
            else:
                new = max(1, min(60, new))
            await save_caldav_config(session, {key: new})
    await refresh_calendar_connection()
    await _edit_or_send(
        cq, await build_calendar_cfg_text(), await _calendar_cfg_kb()
    )


# --------------------------------------------------------------------------
# FSM handlers for the new panels
# --------------------------------------------------------------------------
async def on_qr_text(message: Message, state: FSMContext) -> None:
    if not await _guard(message, "fsm:qr_text"):
        return
    await state.clear()
    raw = (message.text or "").strip()
    if not raw:
        await message.answer("Пустой текст — отменено.")
        return
    if "|" in raw:
        category, _, text = raw.partition("|")
        category, text = category.strip() or "general", text.strip()
    else:
        category, text = "general", raw
    if not text:
        await message.answer("Пустой текст — отменено.")
        return
    async with SessionLocal() as session:
        session.add(QuickReply(text=text[:255], category=category[:64]))
        await session.commit()
    await message.answer("✅ Заготовка сохранена", reply_markup=_back_kb())


async def on_delay_input(message: Message, state: FSMContext) -> None:
    if not await _guard(message, "fsm:delay_input"):
        return
    await state.clear()
    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer("Нужно два числа, например «60 180».")
        return
    try:
        lo, hi = int(parts[0]), int(parts[1])
    except ValueError:
        await message.answer("Это должны быть числа.")
        return
    lo = max(30, min(600, lo))
    hi = max(lo, min(600, hi))
    async with SessionLocal() as session:
        delay = await get_delay_settings(session)
        await save_delay_settings(session, delay.enabled, lo, hi)
    await message.answer(
        f"✅ Задержка ответа: {lo}–{hi} с", reply_markup=_back_kb()
    )


async def on_rag_search(message: Message, state: FSMContext) -> None:
    if not await _guard(message, "fsm:rag_search"):
        return
    await state.clear()
    query = (message.text or "").strip()
    if not query:
        await message.answer("Пустой запрос.")
        return
    from .rag_engine import rag_engine

    if not await rag_engine.initialize():
        await message.answer(
            f"RAG недоступен: {_esc(rag_engine.last_error or '—')}"
        )
        return
    results = await rag_engine.search_cross_chat(query, limit=8, min_similarity=0.0)
    if not results:
        await message.answer("Ничего не найдено.")
        return
    lines = [f"🔎 <b>Результаты по «{_esc(query)}»</b>", "──────────────────"]
    for r in results:
        score = int(getattr(r, "similarity_score", 0) * 100)
        lines.append(
            f"• [{score}%] {_esc(r.chat_name or '')} / "
            f"{_esc(r.sender_name or '')}:\n«{_esc((r.text or '')[:200])}»"
        )
    await message.answer(_clip("\n".join(lines)), reply_markup=_back_kb())


async def on_llm_test(message: Message, state: FSMContext) -> None:
    if not await _guard(message, "fsm:llm_test"):
        return
    await state.clear()
    text = (message.text or "").strip()
    if not text:
        await message.answer("Пустой текст.")
        return
    from .llm_engine import LLMUnavailableError, get_client
    from .style_engine import get_latest_profile

    await message.answer("⏳ Генерирую…")
    async with SessionLocal() as session:
        profile = await get_latest_profile(session)
    try:
        variants = await get_client().generate_reply(
            incoming_text=text,
            sender_name="Тестовый собеседник",
            style_profile=profile,
            chat_history=[],
        )
    except LLMUnavailableError as e:
        await message.answer(f"LLM недоступна: {_esc(str(e))}")
        return
    except Exception as e:  # noqa: BLE001
        await message.answer(f"Ошибка генерации: {_esc(str(e))}")
        return
    lines = ["🧪 <b>Варианты ответа</b>", "──────────────────"]
    for i, v in enumerate(variants, 1):
        lines.append(f"{i}. {_esc(v)}")
    await message.answer("\n".join(lines), reply_markup=_back_kb())


async def on_meeting_kw(message: Message, state: FSMContext) -> None:
    if not await _guard(message, "fsm:meeting_kw"):
        return
    await state.clear()
    phrase = (message.text or "").strip().lower()
    if not phrase or len(phrase) > 80:
        await message.answer("Фраза пустая или длиннее 80 символов.")
        return
    from . import meeting_detector

    async with SessionLocal() as session:
        raw = await get_setting(session, "meeting_phrases", "[]")
        try:
            user = [p for p in json.loads(raw) if isinstance(p, str)]
        except (ValueError, TypeError):
            user = []
        if phrase not in user:
            user.append(phrase)
        await set_setting(
            session, "meeting_phrases", json.dumps(user, ensure_ascii=False)
        )
    meeting_detector.set_user_keywords(user)
    await message.answer("✅ Фраза добавлена", reply_markup=_back_kb())


async def on_owner_id(message: Message, state: FSMContext) -> None:
    if not await _guard(message, "fsm:owner_id"):
        return
    await state.clear()
    value = (message.text or "").strip()
    try:
        int(value)
    except ValueError:
        await message.answer("owner chat_id должен быть числом.")
        return
    async with SessionLocal() as session:
        await set_setting(session, "owner_chat_id", value)
    await message.answer(
        f"✅ owner chat_id сохранён: {value}", reply_markup=_back_kb()
    )


async def on_calendar_event(message: Message, state: FSMContext) -> None:
    if not await _guard(message, "fsm:calendar_event"):
        return
    await state.clear()
    from .calendar_engine import calendar_engine

    raw = (message.text or "").strip()
    bits = [b.strip() for b in raw.split("|")]
    if len(bits) < 2:
        await message.answer(
            "Формат: Название | ГГГГ-ММ-ДД ЧЧ:ММ | длительность_мин"
        )
        return
    title = bits[0]
    try:
        start = datetime.strptime(bits[1], "%Y-%m-%d %H:%M")
    except ValueError:
        await message.answer("Не понял дату. Пример: 2026-05-22 15:00")
        return
    try:
        duration = int(bits[2]) if len(bits) > 2 and bits[2] else 60
    except ValueError:
        duration = 60
    end = start + timedelta(minutes=duration)
    if not calendar_engine.is_connected:
        await message.answer("Календарь не подключён.")
        return
    uid = await calendar_engine.create_event(
        title=title, start=start, end=end, description="", location=""
    )
    if not uid:
        await message.answer(
            f"Не удалось создать событие: {_esc(calendar_engine.last_error or '—')}"
        )
        return
    async with SessionLocal() as session:
        session.add(
            CreatedMeeting(
                chat_id=0,
                calendar_uid=uid,
                title=title,
                start_time=start,
                end_time=end,
                created_by="admin_bot",
            )
        )
        await session.commit()
    await message.answer(
        f"✅ Событие создано: {_esc(title)}\n🗓 {start:%d.%m %H:%M}–{end:%H:%M}",
        reply_markup=_back_kb(),
    )


async def on_repl_dir(message: Message, state: FSMContext) -> None:
    if not await _guard(message, "fsm:repl_dir"):
        return
    await state.clear()
    path = (message.text or "").strip()
    if not path:
        await message.answer("Пустой путь.")
        return
    from .replication import save_settings as save_repl_settings

    async with SessionLocal() as session:
        await save_repl_settings(session, target_dir=path)
    await message.answer(
        f"✅ Папка для копий: {_esc(path)}", reply_markup=_back_kb()
    )


async def on_repl_interval(message: Message, state: FSMContext) -> None:
    if not await _guard(message, "fsm:repl_interval"):
        return
    await state.clear()
    try:
        minutes = int((message.text or "").strip())
    except ValueError:
        await message.answer("Нужно число минут.")
        return
    minutes = max(1, min(10080, minutes))
    from .replication import save_settings as save_repl_settings

    async with SessionLocal() as session:
        await save_repl_settings(session, interval_minutes=minutes)
    await message.answer(
        f"✅ Интервал репликации: {minutes} мин", reply_markup=_back_kb()
    )


async def on_temps_input(message: Message, state: FSMContext) -> None:
    if not await _guard(message, "fsm:temps"):
        return
    await state.clear()
    parts = (message.text or "").replace(",", " ").split()
    try:
        temps = [float(p) for p in parts]
    except ValueError:
        await message.answer("Нужны 3 числа, например «0.7 0.85 1.0».")
        return
    if len(temps) != 3 or any(t < 0 or t > 2 for t in temps):
        await message.answer("Нужно ровно 3 числа в диапазоне 0–2.")
        return
    async with SessionLocal() as session:
        await set_setting(session, "reply_temperatures", json.dumps(temps))
    await message.answer(
        f"✅ Температуры ответов: {temps}", reply_markup=_back_kb()
    )


async def on_settle_input(message: Message, state: FSMContext) -> None:
    if not await _guard(message, "fsm:settle"):
        return
    await state.clear()
    try:
        value = int((message.text or "").strip())
    except ValueError:
        await message.answer("Нужно число секунд.")
        return
    value = max(0, min(120, value))
    async with SessionLocal() as session:
        await set_setting(session, "reply_settle_seconds", str(value))
    await message.answer(
        f"✅ Сборка ответа: {value} с тишины", reply_markup=_back_kb()
    )


async def on_history_limit_input(message: Message, state: FSMContext) -> None:
    if not await _guard(message, "fsm:history_limit"):
        return
    await state.clear()
    try:
        value = int((message.text or "").strip())
    except ValueError:
        await message.answer("Нужно число.")
        return
    value = max(2, min(80, value))
    async with SessionLocal() as session:
        await set_setting(session, "reply_history_limit", str(value))
    await message.answer(
        f"✅ Глубина истории: {value} сообщений", reply_markup=_back_kb()
    )


# --------------------------------------------------------------------------
# Extra commands
# --------------------------------------------------------------------------
async def cmd_menu(message: Message, state: FSMContext) -> None:
    await cmd_start(message, state)


async def cmd_settings(message: Message) -> None:
    if not await _guard(message, "/settings"):
        return
    async with SessionLocal() as session:
        kb = await settings_menu_kb(session)
    await message.answer("⚙️ <b>Настройки</b>", reply_markup=kb)


async def cmd_training(message: Message) -> None:
    if not await _guard(message, "/training"):
        return
    from .trainer import training_state

    await message.answer(
        await build_training_text(),
        reply_markup=_training_kb(training_state.running),
    )


async def cmd_replication(message: Message) -> None:
    if not await _guard(message, "/replication"):
        return
    await message.answer(
        await build_replication_text(), reply_markup=await _replication_kb()
    )


async def cmd_rag(message: Message) -> None:
    if not await _guard(message, "/rag"):
        return
    await message.answer(await build_rag_text(), reply_markup=await _rag_kb())


async def cmd_dialogs(message: Message) -> None:
    if not await _guard(message, "/dialogs"):
        return
    await message.answer(
        "🗂 <b>Бэкап диалогов</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [_btn("Открыть раздел", "dlg:chats")],
        ]),
    )


async def cmd_quick(message: Message) -> None:
    if not await _guard(message, "/quick"):
        return
    text, kb = await _quick_payload()
    await message.answer(text, reply_markup=kb)


async def cmd_style(message: Message) -> None:
    if not await _guard(message, "/style"):
        return
    await message.answer("🎭 <b>Профиль стиля</b>", reply_markup=style_menu_kb())
