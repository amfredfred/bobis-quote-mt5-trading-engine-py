"""Tests for LossTracker — daily reset behaviour."""

from __future__ import annotations

from datetime import date
from zoneinfo import ZoneInfo
from unittest.mock import patch

import pytest

from src.risk.loss_tracker import LossTracker

UTC = ZoneInfo("UTC")


def _make_tracker(**kwargs) -> LossTracker:
    defaults = dict(max_daily_loss_pct=5.0, engine_tz=UTC)
    defaults.update(kwargs)
    return LossTracker(**defaults)


def _day_ms(d: date) -> int:
    """Epoch-ms for midnight UTC of a given date."""
    from datetime import datetime, timezone
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)


# ── Daily reset ───────────────────────────────────────────────────────────────

def test_first_poll_latches_start_equity():
    tracker = _make_tracker()
    day = date(2026, 1, 6)
    with patch("src.risk.loss_tracker._today", return_value=day), \
         patch("src.risk.loss_tracker._now_ms", return_value=_day_ms(day)):
        tracker.update_daily_loss_pct(0.0, 10_000.0)
    assert tracker.stats()["start_of_day_equity"] == 10_000.0
    assert tracker.stats()["equity_peak"] == 10_000.0


def test_day_boundary_resets_state():
    tracker = _make_tracker()
    day1 = date(2026, 1, 6)
    day2 = date(2026, 1, 7)

    with patch("src.risk.loss_tracker._today", return_value=day1), \
         patch("src.risk.loss_tracker._now_ms", return_value=_day_ms(day1)):
        tracker.update_daily_loss_pct(0.0, 10_000.0)
        tracker.update_equity(10_500.0)          # peak raised intraday
        tracker.update_daily_loss_pct(5.0, 10_000.0)  # triggers pause

    with patch("src.risk.loss_tracker._now_ms", return_value=_day_ms(day1) + 3_600_000):
        paused, _ = tracker.is_paused()
        assert paused

    # New day with valid start_equity
    with patch("src.risk.loss_tracker._today", return_value=day2), \
         patch("src.risk.loss_tracker._now_ms", return_value=_day_ms(day2)):
        tracker.update_daily_loss_pct(0.0, 9_700.0)

    stats = tracker.stats()
    assert stats["start_of_day_equity"] == 9_700.0
    assert stats["equity_peak"] == 9_700.0
    assert stats["equity_drawdown_pct"] == 0.0
    with patch("src.risk.loss_tracker._now_ms", return_value=_day_ms(day2) + 60_000):
        paused, _ = tracker.is_paused()
        assert not paused


def test_day_boundary_resets_even_when_start_equity_zero():
    """Guards reset on day change even if broker data is unavailable (start_equity=0).
    start_of_day_equity is latched on the next valid poll."""
    tracker = _make_tracker()
    day1 = date(2026, 1, 6)
    day2 = date(2026, 1, 7)

    with patch("src.risk.loss_tracker._today", return_value=day1), \
         patch("src.risk.loss_tracker._now_ms", return_value=_day_ms(day1)):
        tracker.update_daily_loss_pct(0.0, 10_000.0)
        tracker.update_equity(10_500.0)
        tracker.update_daily_loss_pct(5.0, 10_000.0)  # pause

    # Day boundary — MT5 offline, start_equity=0
    with patch("src.risk.loss_tracker._today", return_value=day2), \
         patch("src.risk.loss_tracker._now_ms", return_value=_day_ms(day2)):
        tracker.update_daily_loss_pct(0.0, 0.0)

    # Guards reset despite invalid equity
    assert tracker.stats()["equity_drawdown_pct"] == 0.0
    paused, _ = tracker.is_paused()
    assert not paused

    # equity not latched yet
    assert tracker.stats()["start_of_day_equity"] == 0.0

    # Next poll — MT5 back online
    with patch("src.risk.loss_tracker._today", return_value=day2), \
         patch("src.risk.loss_tracker._now_ms", return_value=_day_ms(day2) + 30_000):
        tracker.update_daily_loss_pct(0.0, 9_700.0)

    assert tracker.stats()["start_of_day_equity"] == 9_700.0
    assert tracker.stats()["equity_peak"] == 9_700.0


def test_deferred_latch_does_not_overwrite_once_set():
    """start_of_day_equity must not be overwritten mid-day once latched."""
    tracker = _make_tracker()
    day = date(2026, 1, 6)

    with patch("src.risk.loss_tracker._today", return_value=day), \
         patch("src.risk.loss_tracker._now_ms", return_value=_day_ms(day)):
        tracker.update_daily_loss_pct(0.0, 10_000.0)   # latched
        tracker.update_daily_loss_pct(2.0, 9_800.0)    # mid-day, should not overwrite

    assert tracker.stats()["start_of_day_equity"] == 10_000.0


def test_daily_risk_amount_after_zero_start_equity_poll():
    """daily_risk_amount returns 0 until start_equity is latched, then correct value."""
    tracker = _make_tracker(max_daily_loss_pct=5.0)
    day = date(2026, 1, 6)

    with patch("src.risk.loss_tracker._today", return_value=day), \
         patch("src.risk.loss_tracker._now_ms", return_value=_day_ms(day)):
        tracker.update_daily_loss_pct(0.0, 0.0)   # unavailable
        assert tracker.daily_risk_amount(3) == 0.0

        tracker.update_daily_loss_pct(0.0, 10_000.0)  # deferred latch
        # budget = 10_000 × 5% = 500 / 3 slots ≈ 166.67
        assert pytest.approx(tracker.daily_risk_amount(3), rel=1e-4) == 500.0 / 3
