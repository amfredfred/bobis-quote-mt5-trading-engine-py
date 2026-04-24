"""
risk/loss_tracker.py — daily loss % circuit-breaker.

Single responsibility: when the broker-reported daily loss percentage
reaches MAX_DAILY_LOSS_PERCENT, pause all new trade execution until
midnight in engine_timezone.

How it fits in the pipeline:
    1.  PositionManager polls MT5 on every tick and calls
        execution_engine.update_daily_loss(loss_pct).
    2.  ExecutionEngine forwards that value to
        loss_tracker.update_daily_loss_pct(pct).
    3.  RiskEngine runs loss_guard_rule first in ALL_RULES, which calls
        loss_tracker.is_paused() — if True, the signal is rejected before
        any other check runs.

State:
    In-memory only.  No DB hydration needed — the daily_loss_pct is
    fetched live from MT5 on every poll cycle, so state is automatically
    correct after a restart.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, date
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _day_end_ms(day: date, tz: ZoneInfo) -> int:
    """Return the Unix-ms timestamp of the next midnight in *tz*."""
    return int(
        datetime(day.year, day.month, day.day, 0, 0, 0, tzinfo=tz).timestamp() * 1000
    ) + 24 * 3_600_000


def _today(tz: ZoneInfo) -> date:
    return datetime.now(tz=tz).date()


class LossTracker:
    """
    Thread-safe daily loss % circuit-breaker.

    Usage:
        tracker = LossTracker(
            max_daily_loss_pct = config.risk.max_daily_loss_percent,
            engine_tz          = config.engine_timezone,
        )

        # Called on every position-manager poll cycle (via ExecutionEngine):
        tracker.update_daily_loss_pct(loss_pct)

        # Called by loss_guard_rule before every signal evaluation:
        paused, reason = tracker.is_paused()
    """

    def __init__(
        self,
        max_daily_loss_pct: float,
        engine_tz: ZoneInfo,
    ) -> None:
        self._limit   = max_daily_loss_pct
        self._tz      = engine_tz
        self._lock    = threading.Lock()

        self._current_pct:  float = 0.0
        self._paused_until: int   = 0   # Unix-ms; 0 = not paused

    # ── Main update ────────────────────────────────────────────────────────

    def update_daily_loss_pct(self, pct: float) -> None:
        """
        Receive the latest daily loss % from MT5 (via ExecutionEngine).

        If *pct* meets or exceeds MAX_DAILY_LOSS_PERCENT and the session
        is not already paused, set paused_until to end of today and log a
        warning.  The pause is cleared automatically when the calendar day
        rolls over (is_paused() checks the wall-clock timestamp).
        """
        with self._lock:
            self._current_pct = pct

            now = _now_ms()

            # Already paused and still within the pause window — nothing to do.
            if self._paused_until and now < self._paused_until:
                return

            # Daily rollover: clear a stale pause from a previous calendar day.
            if self._paused_until and now >= self._paused_until:
                self._paused_until = 0

            # Trigger: daily loss limit reached.
            if pct >= self._limit:
                today              = _today(self._tz)
                self._paused_until = _day_end_ms(today, self._tz)
                mins_left = int((self._paused_until - now) // 60_000)
                logger.warning(
                    "🔴 Daily loss limit reached: %.2f%% >= %.2f%% — "
                    "pausing trading for %d min (until midnight %s)",
                    pct,
                    self._limit,
                    mins_left,
                    today.isoformat(),
                )

    # ── Guard query ────────────────────────────────────────────────────────

    def is_paused(self) -> tuple[bool, str]:
        """
        Return (paused: bool, reason: str).
        Thread-safe — called from RiskEngine on every signal evaluation.
        """
        with self._lock:
            now = _now_ms()
            if self._paused_until and now < self._paused_until:
                mins = int((self._paused_until - now) // 60_000)
                return (
                    True,
                    f"Daily loss limit hit ({self._current_pct:.2f}% / {self._limit:.2f}%) "
                    f"— {mins} min until midnight reset",
                )
            return False, ""

    # ── Stats for monitoring / logging ─────────────────────────────────────

    def stats(self) -> dict:
        with self._lock:
            now    = _now_ms()
            paused = bool(self._paused_until and now < self._paused_until)
            return {
                "daily_loss_pct":   self._current_pct,
                "paused":           paused,
                "paused_until_ms":  self._paused_until if paused else None,
                "guard_config": {
                    "max_daily_loss_percent": self._limit,
                },
            }
