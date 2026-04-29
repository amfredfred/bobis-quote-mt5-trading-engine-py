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
    Thread-safe daily loss % circuit-breaker + risk budget provider.

    Usage:
        tracker = LossTracker(
            max_daily_loss_pct = config.risk.max_daily_loss_percent,
            engine_tz          = config.engine_timezone,
        )

        # Called on every position-manager poll cycle (via ExecutionEngine):
        tracker.update_daily_loss_pct(loss_pct, start_equity)

        # Called by loss_guard_rule before every signal evaluation:
        paused, reason = tracker.is_paused()

        # Called by TradePlanner for lot sizing:
        risk_amount = tracker.daily_risk_amount(max_losing_streak)
    """

    def __init__(
        self,
        max_daily_loss_pct: float,
        engine_tz: ZoneInfo,
    ) -> None:
        self._limit   = max_daily_loss_pct
        self._tz      = engine_tz
        self._lock    = threading.Lock()

        self._current_pct:       float = 0.0
        self._start_of_day_equity: float = 0.0
        self._paused_until:      int   = 0   # Unix-ms; 0 = not paused
        self._tracked_day:       date | None = None

    # ── Main update ────────────────────────────────────────────────────────

    def update_daily_loss_pct(self, pct: float, start_equity: float) -> None:
        """
        Receive the latest daily loss % and start-of-day equity from MT5
        (via ExecutionEngine).

        start_of_day_equity is latched on the first call each calendar day
        and held fixed until midnight. This ensures lot sizes are stable
        throughout the session regardless of intraday P&L movement.

        If *pct* meets or exceeds MAX_DAILY_LOSS_PERCENT and the session
        is not already paused, set paused_until to end of today and log a
        warning.  The pause is cleared automatically when the calendar day
        rolls over (is_paused() checks the wall-clock timestamp).
        """
        with self._lock:
            self._current_pct = pct

            today = _today(self._tz)
            now   = _now_ms()

            # Latch start-of-day equity once per calendar day.
            # start_equity from positions.py is 0.0 on data failure — ignore those.
            if (self._tracked_day != today) and start_equity > 0:
                self._tracked_day         = today
                self._start_of_day_equity = start_equity
                logger.info(
                    "📅 New trading day %s — start-of-day equity latched at %.2f",
                    today.isoformat(),
                    start_equity,
                )

            # Already paused and still within the pause window — nothing to do.
            if self._paused_until and now < self._paused_until:
                return

            # Daily rollover: clear a stale pause from a previous calendar day.
            if self._paused_until and now >= self._paused_until:
                self._paused_until = 0

            # Trigger: daily loss limit reached.
            if pct >= self._limit:
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

    # ── Risk budget ────────────────────────────────────────────────────────

    def daily_risk_amount(self, max_losing_streak: int) -> float:
        """
        Return the per-trade risk amount in account currency for today.

            daily_budget   = start_of_day_equity × (max_daily_loss_pct / 100)
            risk_per_trade = daily_budget / (max_losing_streak + 1)

        Budget coherence guarantee:
            max_open_trades  = max_losing_streak + 1
            max_exposure     = max_open_trades × risk_per_trade = daily_budget ✓

        Returns 0.0 if start_of_day_equity has not yet been latched
        (first poll cycle of the day has not completed).
        """
        with self._lock:
            if self._start_of_day_equity <= 0:
                logger.warning(
                    "daily_risk_amount: start_of_day_equity not yet latched — "
                    "returning 0.0; lot sizing will use min_lot fallback"
                )
                return 0.0
            daily_budget = self._start_of_day_equity * (self._limit / 100.0)
            return daily_budget / (max_losing_streak + 1)

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
            daily_budget = (
                self._start_of_day_equity * (self._limit / 100.0)
                if self._start_of_day_equity > 0 else 0.0
            )
            return {
                "daily_loss_pct":        self._current_pct,
                "start_of_day_equity":   self._start_of_day_equity,
                "daily_budget":          round(daily_budget, 2),
                "paused":                paused,
                "paused_until_ms":       self._paused_until if paused else None,
                "guard_config": {
                    "max_daily_loss_percent": self._limit,
                },
            }









