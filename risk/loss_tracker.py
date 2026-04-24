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
      Resets ONLY on a TP1 or TP2 hit, or at midnight in engine_timezone.
      MANUAL / EXPIRED / CLOSED_WHILE_DOWN closes do NOT reset the streak —
      they are neutral: neither a loss nor a streak-breaker.

  Guard 2 — daily cap:
      Pause for the remainder of the session day after MAX_DAILY_LOSSES
      losing trades on a single calendar day.

  Guard 3 — rolling window:
      Pause until window expires after MAX_LOSSES_PER_WINDOW losses
      within any rolling LOSS_WINDOW_HOURS period.

State:
  In-memory list of (closed_at_ms, CloseReason | None) tuples, populated
  at startup from TradeRepository (today's trades only) and updated by
  EventBus subscriptions on TRADE_CLOSED / TRADE_SL_HIT.

  Stores the actual CloseReason (not just a bool) so the consecutive-streak
  logic can correctly distinguish wins (TP1/TP2 → reset streak) from
  neutral outcomes (MANUAL → preserve streak).

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

# ── Close-reason classification ───────────────────────────────────────────────

# Reasons that increment the loss counters (streak + daily + window).
_LOSS_REASONS = frozenset({
    CloseReason.SL_HIT,
    CloseReason.INVALIDATED,
    CloseReason.CLOSED_WHILE_DOWN,
    CloseReason.ERROR,
})

# Reasons that reset the consecutive streak (genuine wins).
# Everything else is neutral — it neither counts as a loss nor breaks the streak.
_WIN_REASONS = frozenset({
    CloseReason.TP1_HIT,
    CloseReason.TP2_HIT,
})


def _is_loss(reason: CloseReason | None) -> bool:
    return reason in _LOSS_REASONS


def _is_win(reason: CloseReason | None) -> bool:
    return reason in _WIN_REASONS


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

        # Each entry: (closed_at_ms: int, reason: CloseReason | None)
        # Storing the actual reason (not just a bool) lets the streak logic
        # distinguish wins (reset streak) from neutral closes (preserve streak).
        self._history:      list[tuple[int, CloseReason | None]] = []
        self._paused_until: int                                   = 0

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

        today     = _today(self._tz)
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
                self._history.append((t.closed_at, t.close_reason))
            self._recompute_pause()

        logger.info(
            "LossTracker: loaded %d trades from today (%s)  "
            "losses=%d  paused=%s",
            len(today_closed),
            today.isoformat(),
            sum(1 for _, r in self._history if _is_loss(r)),
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

        with self._lock:
            # Daily rollover: drop history entries from before today
            self._history = [
                (ts, r) for ts, r in self._history if ts >= day_start
            ]
            self._history.append((trade.closed_at, trade.close_reason))
            self._recompute_pause()

            # Snapshot stats under the lock so the log line is consistent
            streak       = self._consecutive_losses_nolock()
            daily_losses = self._daily_losses_nolock()
            paused       = bool(self._paused_until and _now_ms() < self._paused_until)

        outcome = "LOSS" if _is_loss(trade.close_reason) else (
            "WIN" if _is_win(trade.close_reason) else "NEUTRAL"
        )
        logger.info(
            "LossTracker: trade closed  %s  reason=%s  outcome=%s  "
            "streak=%d  daily_losses=%d  paused=%s",
            trade.id,
            trade.close_reason.value if trade.close_reason else "?",
            outcome,
            streak,
            daily_losses,
            paused,
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
                # Pause from the most recent loss in the streak
                last_loss_ts = next(
                    (ts for ts, r in reversed(self._history) if _is_loss(r)),
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
            loss_times = [ts for ts, r in self._history if _is_loss(r)]
            for start_ts in loss_times:
                window_count = sum(
                    1 for ts in loss_times
                    if 0 <= ts - start_ts <= self._window_ms
                )
                if window_count >= self._max_window:
                    # Pause expires when the oldest triggering loss falls
                    # outside the window — i.e. start_ts + window_ms.
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
        """
        Count the current losing streak from the most recent trade backwards.

        Rules (matches docstring):
          - Loss (SL_HIT etc.)  → increment counter
          - Win  (TP1/TP2)      → stop, streak is broken
          - Neutral (MANUAL…)   → skip; neither counts as a loss nor resets the streak
        """
        count = 0
        for _, reason in reversed(self._history):
            if _is_loss(reason):
                count += 1
            elif _is_win(reason):
                break
            # neutral: keep walking backward without incrementing or breaking

        return count

    def _daily_losses_nolock(self) -> int:
        today     = _today(self._tz)
        day_start = _day_start_ms(today, self._tz)
        return sum(
            1 for ts, r in self._history
            if _is_loss(r) and ts >= day_start
        )
