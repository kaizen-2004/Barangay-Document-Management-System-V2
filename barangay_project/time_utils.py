"""Time helpers for consistent UTC handling."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

LOCAL_TIMEZONE = ZoneInfo("Asia/Manila")


def utcnow() -> datetime:
    """Return a naive UTC datetime for DB storage and comparisons."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_local_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(LOCAL_TIMEZONE)


def format_local_datetime(value: datetime | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    local_value = to_local_datetime(value)
    return local_value.strftime(fmt) if local_value else "—"
