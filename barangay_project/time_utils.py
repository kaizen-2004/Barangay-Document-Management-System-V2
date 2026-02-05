"""Time helpers for consistent UTC handling."""

from datetime import datetime, timezone


def utcnow() -> datetime:
    """Return a naive UTC datetime for DB storage and comparisons."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
