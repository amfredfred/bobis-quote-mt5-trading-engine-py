"""RejectionCandle.from_dict's pattern deserialization — must not drop a
whole signal just because its CandlePattern value isn't in this codebase's
copy of the enum yet (see the 2026-07-19/20 incident: BOS_LONG/BOS_SHORT/
FVG_SHORT signals silently failed to execute because this file's
CandlePattern hadn't been updated for the new bos_pullback/fvg strategies)."""

from __future__ import annotations

import logging

from src.domain.signal_interface import CandlePattern, RejectionCandle


def _payload(pattern: str) -> dict:
    return {
        "open": 100.0,
        "high": 101.0,
        "low": 99.0,
        "close": 100.5,
        "timestamp": 1_780_000_000_000,
        "wickRatio": 0.5,
        "pattern": pattern,
        "wickTip": 99.0,
    }


def test_known_pattern_deserializes_normally():
    rc = RejectionCandle.from_dict(_payload("BOS_LONG"))
    assert rc.pattern == CandlePattern.BOS_LONG


def test_all_current_signal_engine_patterns_are_recognized():
    """Regression guard: every CandlePattern Signal Engine's domain/entities/
    enums.py currently emits must round-trip without falling back to
    UNKNOWN. Update this list (and CandlePattern above) whenever Signal
    Engine adds a strategy."""
    current_patterns = [
        "SHOOTING_STAR", "HAMMER", "CRT_BUY", "CRT_SELL",
        "ORB_LONG", "ORB_SHORT", "BOS_LONG", "BOS_SHORT",
        "FVG_LONG", "FVG_SHORT",
    ]
    for name in current_patterns:
        rc = RejectionCandle.from_dict(_payload(name))
        assert rc.pattern != CandlePattern.UNKNOWN, f"{name} fell back to UNKNOWN"
        assert rc.pattern.value == name


def test_unrecognized_pattern_falls_back_to_unknown_not_a_crash():
    rc = RejectionCandle.from_dict(_payload("SOME_FUTURE_STRATEGY_PATTERN"))
    assert rc.pattern == CandlePattern.UNKNOWN
    # The rest of the candle still deserialized correctly — the whole
    # signal isn't dropped just because the pattern name is unrecognized.
    assert rc.open == 100.0
    assert rc.close == 100.5


def test_unrecognized_pattern_logs_a_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="src.domain.signal_interface"):
        RejectionCandle.from_dict(_payload("SOME_FUTURE_STRATEGY_PATTERN"))
    assert any("Unrecognized CandlePattern" in r.message for r in caplog.records)
