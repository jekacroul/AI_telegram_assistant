from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_setting, set_setting

SETTING_DELAY_ENABLED = "delay_enabled"
SETTING_DELAY_MIN_SECONDS = "delay_min_seconds"
SETTING_DELAY_MAX_SECONDS = "delay_max_seconds"

DEFAULT_DELAY_ENABLED = False
DEFAULT_DELAY_MIN_SECONDS = 60
DEFAULT_DELAY_MAX_SECONDS = 180
MIN_DELAY_SECONDS = 30
MAX_DELAY_SECONDS = 600

_TRUE_VALUES = {"1", "true", "True", "yes", "on"}


@dataclass(frozen=True)
class DelaySettings:
    enabled: bool = DEFAULT_DELAY_ENABLED
    min_seconds: int = DEFAULT_DELAY_MIN_SECONDS
    max_seconds: int = DEFAULT_DELAY_MAX_SECONDS


def _coerce_seconds(value: object, default: int) -> int:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        seconds = default
    return min(MAX_DELAY_SECONDS, max(MIN_DELAY_SECONDS, seconds))


def normalize_delay_settings(
    enabled: bool,
    min_seconds: object,
    max_seconds: object,
) -> DelaySettings:
    min_value = _coerce_seconds(min_seconds, DEFAULT_DELAY_MIN_SECONDS)
    max_value = _coerce_seconds(max_seconds, DEFAULT_DELAY_MAX_SECONDS)
    if min_value > max_value:
        min_value, max_value = max_value, min_value
    return DelaySettings(
        enabled=bool(enabled),
        min_seconds=min_value,
        max_seconds=max_value,
    )


async def get_delay_settings(session: AsyncSession) -> DelaySettings:
    enabled_raw = await get_setting(
        session, SETTING_DELAY_ENABLED, "1" if DEFAULT_DELAY_ENABLED else "0"
    )
    min_raw = await get_setting(
        session, SETTING_DELAY_MIN_SECONDS, str(DEFAULT_DELAY_MIN_SECONDS)
    )
    max_raw = await get_setting(
        session, SETTING_DELAY_MAX_SECONDS, str(DEFAULT_DELAY_MAX_SECONDS)
    )
    return normalize_delay_settings(
        enabled_raw in _TRUE_VALUES,
        min_raw,
        max_raw,
    )


async def save_delay_settings(
    session: AsyncSession,
    enabled: bool,
    min_seconds: object,
    max_seconds: object,
) -> DelaySettings:
    delay = normalize_delay_settings(enabled, min_seconds, max_seconds)
    await set_setting(session, SETTING_DELAY_ENABLED, "1" if delay.enabled else "0")
    await set_setting(session, SETTING_DELAY_MIN_SECONDS, str(delay.min_seconds))
    await set_setting(session, SETTING_DELAY_MAX_SECONDS, str(delay.max_seconds))
    return delay


def delay_to_dict(delay: DelaySettings) -> dict:
    return {
        "delay_enabled": delay.enabled,
        "delay_min_seconds": delay.min_seconds,
        "delay_max_seconds": delay.max_seconds,
    }
