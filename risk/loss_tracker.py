"""
risk/loss_tracker.py — execution-side loss tracking for circuit-breaker guards.

Mirrors the three guards in the Signal Engine's SessionMemory, but operates
on real broker-confirmed trade closures rather than signal outcomes.

Why here and not just daily_loss_pct from MT5:
  - daily_loss_pct is a monetary % — it tells you how much you've lost but
    not how many individual trades have lost, or how recently.
  - The three guards are trade-count based: they stop you after N losses
    regardless of their size. A run of small losses at low RR pairs is just
    as indicative of a bad-regime day as a big loss on a high-RR setup.

Guards:
  Guard 1 — consecutive streak:
      Pause after MAX_CONSECUTIVE_LOSSES losing trades in a row.
      Resets on any TP1/TP2 hit or at midnight in engine_timezone.

  Guard 2 — daily cap:
      Pause for the remainder of the session day after MAX_DAILY_LOSSES
      losing trades on a single calendar day.

  Guard 3 — rolling window:
      Pause until window expires after MAX_LOSSES_PER_WINDOW losses
      within any rolling LOSS_WINDOW_HOURS period.

State:
  In-memory list of (closed_at_ms, is_loss) tuples, populated at startup
  from TradeRepository (today's trades only) and updated by EventBus
  subscriptions on TRADE_CLOSED / TRADE_SL_HIT.

  Survives restarts: the DB load on startup reconstructs today's state.
  Between restarts: in-memory only — no separate file written.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, date, timezone
from typing import TYPE_CHECKING, Optional
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from interfaces.trade import Trade
    from storage.trade_repository import TradeRepository

from interfaces.trade import CloseReason, TradeStatus

logger = logging.getLogger(__name__)

# Close reasons that count as a loss for guard purposes
_LOSS_REASONS = frozenset({
    CloseReason.SL_HIT,
    CloseReason.INVALIDATED,
    CloseReason.CLOSED_WHILE_DOWN,
    CloseReason.ERROR,
})

# Wins / neutrals that reset the consecutive streak
_WIN_REASONS = frozenset({
    CloseReason.TP1_HIT,
    CloseReason.TP2_HIT,
})


def _now_ms() -> int:
    return int(time.time() * 1000)


def _day_start_ms(day: date, tz: ZoneInfo) -> int:
    return int(datetime(day.year, day.month, day.day, 0, 0, 0, tzinfo=tz).timestamp() * 1000)


def _day_end_ms(day: date, tz: ZoneInfo) -> int:
    return _day_start_ms(day, tz) + 24 * 3_600_000


def _today(tz: ZoneInfo) -> date:
    return datetime.now(tz=tz).date()


class LossTracker:
    """
    Thread-safe tracker of closed trade outcomes for circuit-breaker guards.

    Usage:
        tracker = LossTracker(config.risk, config.engine_timezone)
        tracker.load_today(trade_repo)          # call once at startup
        event_bus.on(Events.TRADE_CLOSED, tracker.on_trade_closed)

        # In risk evaluation:
        paused, reason = tracker.is_paused()
    """

    def __init__(
        self,
        max_consecutive:  int,
        pause_hours:      float,
        max_daily:        int,
        max_per_window:   int,
        window_hours:     float,
        engine_tz:        ZoneInfo,
    ) -> None:
        self._max_consec    = max_consecutive
        self._pause_ms      = int(pause_hours * 3_600_000)
        self._max_daily     = max_daily
        self._max_window    = max_per_window
        self._window_ms     = int(window_hours * 3_600_000)
        self._tz            = engine_tz
        self._lock          = threading.Lock()

        # Each entry: (closed_at_ms: int, is_loss: bool)
        self._history:      list[tuple[int, bool]] = []
        self._paused_until: int                    = 0

    # ── Startup hydration ──────────────────────────────────────────────────

    def load_today(self, repo: "TradeRepository") -> None:
        """
        Load today's closed trades from SQLite on startup.
        Rebuilds in-memory history so guard state survives process restarts.
        """
        try:
            all_trades = repo.load_all()
        except Exception as exc:
            logger.warning("LossTracker: could not load trades from DB: %s", exc)
            return

        today   = _today(self._tz)
        day_start = _day_start_ms(today, self._tz)
        day_end   = _day_end_ms(today, self._tz)

        today_closed = [
            t for t in all_trades
            if t.closed_at
            and day_start <= t.closed_at < day_end
            and t.status in (TradeStatus.CLOSED, TradeStatus.CANCELLED)
        ]
        today_closed.sort(key=lambda t: t.closed_at)

        with self._lock:
            self._history.clear()
            for t in today_closed:
                is_loss = t.close_reason in _LOSS_REASONS
                self._history.append((t.closed_at, is_loss))
            self._recompute_pause()

        logger.info(
            "LossTracker: loaded %d trades from today (%s)  "
            "losses=%d  paused=%s",
            len(today_closed),
            today.isoformat(),
            sum(1 for _, is_loss in self._history if is_loss),
            bool(self._paused_until and _now_ms() < self._paused_until),
        )

    # ── EventBus listener ──────────────────────────────────────────────────

    def on_trade_closed(self, trade: "Trade") -> None:
        """
        Subscribe to Events.TRADE_CLOSED.
        Called from the position manager thread — must be thread-safe.
        """
        if not trade.closed_at:
            return

        # Only roll over to today's view — ignore trades closed before today
        today     = _today(self._tz)
        day_start = _day_start_ms(today, self._tz)
        if trade.closed_at < day_start:
            return

        is_loss = trade.close_reason in _LOSS_REASONS

        with self._lock:
            # Daily rollover: drop history entries from before today
            self._history = [
                (ts, l) for ts, l in self._history if ts >= day_start
            ]
            self._history.append((trade.closed_at, is_loss))
            self._recompute_pause()

        outcome = "LOSS" if is_loss else "WIN/NEUTRAL"
        logger.info(
            "LossTracker: trade closed  %s  reason=%s  outcome=%s  "
            "streak=%d  daily_losses=%d  paused=%s",
            trade.id,
            trade.close_reason.value if trade.close_reason else "?",
            outcome,
            self._consecutive_losses_nolock(),
            self._daily_losses_nolock(),
            bool(self._paused_until and _now_ms() < self._paused_until),
        )

    # ── Public guard query ─────────────────────────────────────────────────

    def is_paused(self) -> tuple[bool, str]:
        """
        Return (paused: bool, reason: str).
        Thread-safe — can be called from any thread.
        """
        with self._lock:
            now = _now_ms()
            if self._paused_until and now < self._paused_until:
                mins = int((self._paused_until - now) // 60_000)
                return True, f"Loss guard active — {mins}min remaining"
            return False, ""

    # ── Stats for monitoring / logging ────────────────────────────────────

    def stats(self) -> dict:
        with self._lock:
            now    = _now_ms()
            paused = bool(self._paused_until and now < self._paused_until)
            return {
                "consecutive_losses": self._consecutive_losses_nolock(),
                "daily_losses":       self._daily_losses_nolock(),
                "paused":             paused,
                "paused_until_ms":    self._paused_until if paused else None,
                "guard_config": {
                    "max_consecutive_losses": self._max_consec,
                    "max_daily_losses":       self._max_daily,
                    "max_losses_per_window":  self._max_window,
                    "loss_window_hours":      self._window_ms / 3_600_000,
                },
            }

    # ── Internal — all called under self._lock ─────────────────────────────

    def _recompute_pause(self) -> None:
        """Re-evaluate all three guards and set _paused_until to the latest."""
        now        = _now_ms()
        candidates: list[int] = []

        # Guard 1 — consecutive streak
        if self._max_consec > 0:
            cl = self._consecutive_losses_nolock()
            if cl >= self._max_consec:
                # Pause from the most recent loss
                last_loss_ts = next(
                    (ts for ts, is_loss in reversed(self._history) if is_loss),
                    now,
                )
                pause_until = last_loss_ts + self._pause_ms
                if pause_until > now:
                    candidates.append(pause_until)
                    logger.warning(
                        "🔴 EX Guard 1 (streak): %d consecutive losses — "
                        "pausing %.1fh",
                        cl, self._pause_ms / 3_600_000,
                    )

        # Guard 2 — daily cap
        if self._max_daily > 0:
            dl = self._daily_losses_nolock()
            if dl >= self._max_daily:
                today       = _today(self._tz)
                pause_until = _day_end_ms(today, self._tz)
                if pause_until > now:
                    candidates.append(pause_until)
                    logger.warning(
                        "🔴 EX Guard 2 (daily cap): %d losses today — "
                        "pausing until end of session day",
                        dl,
                    )

        # Guard 3 — rolling window
        if self._max_window > 0 and self._window_ms > 0:
            loss_times = [ts for ts, is_loss in self._history if is_loss]
            for start_ts in loss_times:
                window_count = sum(
                    1 for ts in loss_times
                    if 0 <= ts - start_ts <= self._window_ms
                )
                if window_count >= self._max_window:
                    pause_until = start_ts + self._window_ms
                    if pause_until > now:
                        candidates.append(pause_until)
                        logger.warning(
                            "🔴 EX Guard 3 (window): %d losses in %.1fh window — "
                            "pausing until window expires",
                            window_count, self._window_ms / 3_600_000,
                        )
                    break

        self._paused_until = max(candidates) if candidates else 0

    def _consecutive_losses_nolock(self) -> int:
        count = 0
        for _, is_loss in reversed(self._history):
            if is_loss:
                count += 1
            else:
                break
        return count

    def _daily_losses_nolock(self) -> int:
        today     = _today(self._tz)
        day_start = _day_start_ms(today, self._tz)
        return sum(
            1 for ts, is_loss in self._history
            if is_loss and ts >= day_start
        )
