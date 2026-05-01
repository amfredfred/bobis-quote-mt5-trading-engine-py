"""
risk/loss_tracker.py — daily loss % circuit-breaker + intraday capital guards.

Single responsibility: pause all new trade execution when any of the three
guards below fires, and resume automatically at midnight in engine_timezone.

Guards (parity with Node pipeline):
  1. Daily loss      — when broker-reported daily loss % >= MAX_DAILY_LOSS_PERCENT.
  2. Equity peak     — when equity drops >= MAX_EQUITY_DRAWDOWN_PERCENT from the
                        session high-water mark (resets at midnight).
  3. Rolling window  — when the peak-to-trough drawdown within the last
                        ROLLING_WINDOW_SIZE equity samples >= ROLLING_DRAWDOWN_PCT %.
                        Both params must be > 0 for this guard to be active.

All three guards share a single _paused_until timestamp (same as Node).
The first guard to fire wins and holds the pause until midnight.

How it fits in the pipeline:
    1. PositionManager polls MT5 on every tick, calling
       execution_engine.update_daily_loss(loss_pct, start_equity, current_equity).
    2. ExecutionEngine forwards to:
         a. loss_tracker.update_daily_loss_pct(pct, start_equity)  — guards 1
         b. loss_tracker.update_equity(current_equity)             — guards 2 & 3
    3. RiskEngine runs loss_guard_rule first, which calls loss_tracker.is_paused().

State:
    In-memory only.  No DB hydration needed — the daily_loss_pct is fetched
    live from MT5 on every poll cycle, so state is automatically correct after
    a restart. equityWindow refills in ~2 minutes at 5 s polling; equityPeak
    resets conservatively from current equity on restart — safe, not dangerous.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from datetime import datetime, date
from typing import Deque
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
    Thread-safe daily loss % circuit-breaker + equity guards + risk budget provider.

    Usage:
        tracker = LossTracker(
            max_daily_loss_pct       = config.risk.max_daily_loss_percent,
            engine_tz                = config.engine_timezone,
            max_equity_drawdown_pct  = config.risk.max_equity_drawdown_percent,
            rolling_window_size      = config.risk.rolling_window_size,
            rolling_drawdown_pct     = config.risk.rolling_drawdown_pct,
        )

        # Called by ExecutionEngine on every position-manager poll cycle:
        tracker.update_daily_loss_pct(loss_pct, start_equity)   # guard 1
        tracker.update_equity(current_equity)                    # guards 2 & 3

        # Called by loss_guard_rule before every signal evaluation:
        paused, reason = tracker.is_paused()

        # Called by TradePlanner for lot sizing:
        risk_amount = tracker.daily_risk_amount(max_losing_streak)
    """

    def __init__(
        self,
        max_daily_loss_pct: float,
        engine_tz: ZoneInfo,
        max_equity_drawdown_pct: float = 2.0,
        rolling_window_size: int = 0,
        rolling_drawdown_pct: float = 0.0,
    ) -> None:
        self._limit                 = max_daily_loss_pct
        self._tz                    = engine_tz
        self._max_equity_drawdown   = max_equity_drawdown_pct   # 0 = disabled
        self._rolling_window_size   = rolling_window_size       # 0 = disabled
        self._rolling_drawdown_pct  = rolling_drawdown_pct      # 0 = disabled
        self._lock                  = threading.Lock()

        # Daily state
        self._current_pct:           float       = 0.0
        self._start_of_day_equity:   float       = 0.0
        self._tracked_day:           date | None = None

        # Shared pause state (all three guards write here — first trigger wins)
        self._paused_until:  int = 0    # Unix-ms; 0 = not paused
        self._pause_reason:  str = ""

        # Guard 2 — equity peak (high-water mark)
        self._equity_peak:         float = 0.0
        self._equity_drawdown_pct: float = 0.0

        # Guard 3 — rolling window (count-based, same as Node)
        self._equity_window: Deque[float] = deque()

    # ── Guard 1: Daily loss update ─────────────────────────────────────────

    def update_daily_loss_pct(self, pct: float, start_equity: float) -> None:
        """
        Receive the latest daily loss % and start-of-day equity from MT5
        (via ExecutionEngine).

        start_of_day_equity is latched on the first call each calendar day
        and held fixed until midnight so lot sizes stay stable throughout the
        session. At each new day the equity peak and window are also reset so
        all guards measure from today's session only.
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
                # Reset intraday guards so they measure from this session only
                self._equity_peak         = 0.0
                self._equity_drawdown_pct = 0.0
                self._equity_window.clear()
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
                self._pause_reason = ""

            # Trigger guard 1: daily loss limit reached.
            if pct >= self._limit:
                self._paused_until = _day_end_ms(today, self._tz)
                self._pause_reason = (
                    f"daily loss limit ({pct:.2f}% >= {self._limit:.2f}%)"
                )
                mins_left = int((self._paused_until - now) // 60_000)
                logger.warning(
                    "🔴 Daily loss limit reached: %.2f%% >= %.2f%% — "
                    "pausing trading for %d min (until midnight %s)",
                    pct, self._limit, mins_left, today.isoformat(),
                )

    # ── Guards 2 & 3: Equity update ───────────────────────────────────────

    def update_equity(self, equity: float) -> None:
        """
        Called on every poll cycle with the current broker equity.

        Drives two independent guards (parity with Node's LossTracker.updateEquity):

          Guard 2 — Equity peak (all-time intraday high-water mark):
            Pauses when (peak - equity) / peak >= MAX_EQUITY_DRAWDOWN_PERCENT.
            Resets at midnight via the daily-rollover logic in update_daily_loss_pct.

          Guard 3 — Rolling window (count-based, not time-based):
            Maintains a fixed-length deque of equity samples (ROLLING_WINDOW_SIZE).
            Pauses when peak-to-trough within the window >= ROLLING_DRAWDOWN_PCT %.
            Requires at least 3 samples for a meaningful reading.

        Both guards write to the shared _paused_until — first trigger wins.
        An already-active pause is never overwritten.
        """
        if equity <= 0:
            return

        with self._lock:
            now   = _now_ms()
            today = _today(self._tz)

            # ── Guard 2: All-time intraday equity peak ─────────────────────
            if equity > self._equity_peak:
                self._equity_peak = equity

            if self._equity_peak > 0:
                self._equity_drawdown_pct = (
                    (self._equity_peak - equity) / self._equity_peak
                ) * 100.0

            peak_limit = self._max_equity_drawdown
            if peak_limit > 0 and self._equity_drawdown_pct >= peak_limit:
                if not self._paused_until or now >= self._paused_until:
                    self._paused_until = _day_end_ms(today, self._tz)
                    self._pause_reason = (
                        f"equity drawdown ({self._equity_drawdown_pct:.2f}% >= "
                        f"{peak_limit:.2f}% from peak {self._equity_peak:.2f})"
                    )
                    mins_left = int((self._paused_until - now) // 60_000)
                    logger.warning(
                        "🔴 Peak drawdown limit hit: %.2f%% >= %.2f%% "
                        "(peak=%.2f, current=%.2f) — pausing for %d min until midnight",
                        self._equity_drawdown_pct, peak_limit,
                        self._equity_peak, equity, mins_left,
                    )

            # ── Guard 3: Rolling window peak-to-trough ─────────────────────
            window_size = self._rolling_window_size
            dd_limit    = self._rolling_drawdown_pct

            if not window_size or not dd_limit:
                return  # feature disabled

            self._equity_window.append(equity)
            if len(self._equity_window) > window_size:
                self._equity_window.popleft()

            # Need at least 3 samples for a meaningful peak-to-trough reading.
            if len(self._equity_window) < 3:
                return

            window_peak   = max(self._equity_window)
            window_trough = min(self._equity_window)
            rolling_dd    = ((window_peak - window_trough) / window_peak) * 100.0

            if rolling_dd >= dd_limit:
                if not self._paused_until or now >= self._paused_until:
                    self._paused_until = _day_end_ms(today, self._tz)
                    self._pause_reason = (
                        f"rolling drawdown ({rolling_dd:.2f}% >= {dd_limit:.2f}% "
                        f"over last {len(self._equity_window)} samples)"
                    )
                    mins_left = int((self._paused_until - now) // 60_000)
                    logger.warning(
                        "🔁 Rolling DD hit: %.2f%% >= %.2f%% "
                        "(window=%d) — pausing for %d min until midnight",
                        rolling_dd, dd_limit, window_size, mins_left,
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
        Covers all three guards — daily loss, equity peak, rolling window.
        Thread-safe — called from RiskEngine on every signal evaluation.
        """
        with self._lock:
            now = _now_ms()
            if self._paused_until and now < self._paused_until:
                mins = int((self._paused_until - now) // 60_000)
                return (
                    True,
                    f"{self._pause_reason} — {mins} min until midnight reset",
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
                "pause_reason":          self._pause_reason if paused else None,
                # Guard 2
                "equity_peak":           self._equity_peak,
                "equity_drawdown_pct":   round(self._equity_drawdown_pct, 4),
                # Guard 3
                "rolling_window_samples": len(self._equity_window),
                "guard_config": {
                    "max_daily_loss_percent":    self._limit,
                    "max_equity_drawdown_pct":   self._max_equity_drawdown,
                    "rolling_window_size":       self._rolling_window_size,
                    "rolling_drawdown_pct":      self._rolling_drawdown_pct,
                },
            }










