"""
src/risk/loss_tracker.py — Fixed version
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
    return (
        int(
            datetime(day.year, day.month, day.day, 0, 0, 0, tzinfo=tz).timestamp()
            * 1000
        )
        + 24 * 3_600_000
    )


def _today(tz: ZoneInfo) -> date:
    return datetime.now(tz=tz).date()


class LossTracker:
    def __init__(
        self,
        max_daily_loss_pct: float,
        engine_tz: ZoneInfo,
        max_equity_drawdown_pct: float = 2.0,
        rolling_window_size: int = 0,
        rolling_drawdown_pct: float = 0.0,
    ) -> None:
        self._limit = max_daily_loss_pct
        self._tz = engine_tz
        self._max_equity_drawdown = max_equity_drawdown_pct
        self._rolling_window_size = rolling_window_size
        self._rolling_drawdown_pct = rolling_drawdown_pct

        self._lock = threading.Lock()

        # Daily state
        self._current_pct: float = 0.0
        self._start_of_day_equity: float = 0.0
        self._tracked_day: date | None = None

        # Pause state
        self._paused_until: int = 0
        self._pause_reason: str = ""

        # Guard 2 — Equity Peak
        self._equity_peak: float = 0.0
        self._equity_drawdown_pct: float = 0.0

        # Guard 3 — Rolling Window
        self._equity_window: Deque[float] = deque(maxlen=rolling_window_size)

    # ── Guard 1: Daily Loss ─────────────────────────────────────────────
    def update_daily_loss_pct(self, pct: float, start_equity: float) -> None:
        with self._lock:
            self._current_pct = pct
            today = _today(self._tz)
            now = _now_ms()

            # New trading day → reset everything
            if (self._tracked_day != today) and start_equity > 0:
                self._tracked_day = today
                self._start_of_day_equity = start_equity
                self._equity_peak = start_equity  # Anchor peak to start of day
                self._equity_drawdown_pct = 0.0
                self._equity_window.clear()
                self._paused_until = 0
                self._pause_reason = ""
                logger.info(
                    "New trading day %s — start equity latched at %.2f",
                    today.isoformat(),
                    start_equity,
                )

            if self._paused_until and now >= self._paused_until:
                self._paused_until = 0
                self._pause_reason = ""

            # Daily loss guard
            if pct >= self._limit:
                self._paused_until = _day_end_ms(today, self._tz)
                self._pause_reason = (
                    f"Daily loss limit reached ({pct:.2f}% >= {self._limit:.2f}%)"
                )
                logger.warning(self._pause_reason)

    # ── Guards 2 & 3: Equity Update ─────────────────────────────────────
    def update_equity(self, equity: float) -> None:
        if equity <= 0:
            return

        with self._lock:
            now = _now_ms()
            today = _today(self._tz)

            # Update equity peak (anchored to start-of-day)
            if self._equity_peak == 0 or equity > self._equity_peak:
                self._equity_peak = equity

            if self._equity_peak > 0:
                self._equity_drawdown_pct = (
                    (self._equity_peak - equity) / self._equity_peak
                ) * 100.0

            # Peak drawdown guard
            if (
                self._max_equity_drawdown > 0
                and self._equity_drawdown_pct >= self._max_equity_drawdown
            ):
                if not self._paused_until or now >= self._paused_until:
                    self._paused_until = _day_end_ms(today, self._tz)
                    self._pause_reason = (
                        f"Equity peak drawdown: {self._equity_drawdown_pct:.2f}% "
                        f"(limit {self._max_equity_drawdown:.2f}%, peak {self._equity_peak:,.2f})"
                    )
                    logger.warning(self._pause_reason)

            # Rolling window guard
            if self._rolling_window_size > 0 and self._rolling_drawdown_pct > 0:
                self._equity_window.append(equity)
                if len(self._equity_window) >= 3:
                    w_peak = max(self._equity_window)
                    w_trough = min(self._equity_window)
                    rolling_dd = ((w_peak - w_trough) / w_peak) * 100.0

                    if rolling_dd >= self._rolling_drawdown_pct:
                        if not self._paused_until or now >= self._paused_until:
                            self._paused_until = _day_end_ms(today, self._tz)
                            self._pause_reason = (
                                f"Rolling drawdown: {rolling_dd:.2f}% "
                                f"(limit {self._rolling_drawdown_pct:.2f}%, window {len(self._equity_window)})"
                            )
                            logger.warning(self._pause_reason)

    # ── Public API ──────────────────────────────────────────────────────
    def is_paused(self) -> tuple[bool, str]:
        with self._lock:
            now = _now_ms()
            if self._paused_until and now < self._paused_until:
                mins_left = int((self._paused_until - now) // 60_000)
                reason = self._pause_reason or "Capital Protection Guard Active"
                return True, f"{reason} — {mins_left} min until midnight reset"
            return False, ""

    def daily_risk_amount(self, max_losing_streak: int) -> float:
        with self._lock:
            if self._start_of_day_equity <= 0:
                return 0.0
            daily_budget = self._start_of_day_equity * (self._limit / 100.0)
            return daily_budget / (max_losing_streak + 1)

    def stats(self) -> dict:
        with self._lock:
            now = _now_ms()
            paused = bool(self._paused_until and now < self._paused_until)
            daily_budget = (
                round(self._start_of_day_equity * (self._limit / 100.0), 2)
                if self._start_of_day_equity > 0
                else 0.0
            )

            return {
                "daily_loss_pct": round(self._current_pct, 4),
                "start_of_day_equity": round(self._start_of_day_equity, 2),
                "daily_budget": daily_budget,
                "paused": paused,
                "pause_reason": self._pause_reason if paused else "",
                "equity_peak": round(self._equity_peak, 2),
                "equity_drawdown_pct": round(self._equity_drawdown_pct, 4),
                "rolling_window_samples": len(self._equity_window),
            }
