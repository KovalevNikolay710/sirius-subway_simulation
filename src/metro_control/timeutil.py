"""Time helpers: store UTC, show Europe/Moscow."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

MSK = ZoneInfo("Europe/Moscow")


def _check(dt: datetime) -> None:
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("naive datetime not allowed; attach a timezone")


def to_utc(dt: datetime) -> datetime:
    _check(dt)
    return dt.astimezone(UTC)


def to_msk(dt: datetime) -> datetime:
    _check(dt)
    return dt.astimezone(MSK)
