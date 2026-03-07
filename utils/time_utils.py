"""Time utility helpers."""

from __future__ import annotations

import time
from datetime import datetime, timezone


def now_ms() -> int:
    """Current Unix timestamp in milliseconds."""
    return int(time.time() * 1000)


def now_sec() -> int:
    """Current Unix timestamp in seconds."""
    return int(time.time())


def ms_to_dt(ts_ms: int) -> datetime:
    """Convert a millisecond timestamp to a UTC datetime."""
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)


def today_key() -> str:
    """Return today's date as 'YYYY-MM-DD' (UTC)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def today_start_ms() -> int:
    """Unix ms for 00:00:00 UTC today."""
    d = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return int(d.timestamp() * 1000)


def is_stale(ts_ms: int, max_age_ms: int) -> bool:
    """True if *ts_ms* is older than *max_age_ms* milliseconds."""
    return (now_ms() - ts_ms) > max_age_ms
