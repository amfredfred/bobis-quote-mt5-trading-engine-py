from __future__ import annotations

from src.domain.signal_interface import CandlePattern
from src.utils.comment import MT5_COMMENT_MAX_LEN, build_trade_comment


def test_crt_pattern_produces_expected_tag():
    assert build_trade_comment(CandlePattern.CRT_BUY, "15min", "15min") == "apex-crt-15-15"
    assert build_trade_comment(CandlePattern.CRT_SELL, "1h", "15min") == "apex-crt-1h-15"


def test_bos_pullback_pattern_abbreviates_to_bos():
    assert build_trade_comment(CandlePattern.BOS_LONG, "1h", "5min") == "apex-bos-1h-5"
    assert build_trade_comment(CandlePattern.BOS_SHORT, "1h", "5min") == "apex-bos-1h-5"


def test_fvg_and_orb_patterns():
    assert build_trade_comment(CandlePattern.FVG_LONG, "1h", "1h") == "apex-fvg-1h-1h"
    assert build_trade_comment(CandlePattern.ORB_SHORT, "15min", "15min") == "apex-orb-15-15"


def test_unknown_pattern_falls_back_without_raising():
    assert build_trade_comment(CandlePattern.UNKNOWN, "15min", "15min") == "apex-unk-15-15"


def test_missing_interval_does_not_crash():
    assert build_trade_comment(CandlePattern.CRT_BUY, "", "") == "apex-crt-?-?"


def test_result_never_exceeds_mt5_comment_limit():
    comment = build_trade_comment(CandlePattern.BOS_LONG, "1h", "5min")
    assert len(comment) <= MT5_COMMENT_MAX_LEN
