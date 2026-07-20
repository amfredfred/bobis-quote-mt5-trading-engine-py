"""Builds the MT5 order comment tag: apex-{strategy}-{htf}-{ltf}.

MT5's comment field is capped at 31 characters server-side (an old MQL5
struct limit most brokers still enforce — some truncate silently, others
reject the order outright), so the result is always clamped to that.
"""

from __future__ import annotations

from src.domain.signal_interface import CandlePattern

MT5_COMMENT_MAX_LEN = 31

_PATTERN_TO_STRATEGY = {
    CandlePattern.CRT_BUY: "crt",
    CandlePattern.CRT_SELL: "crt",
    CandlePattern.SHOOTING_STAR: "crt",  # pre-plugin CRT rejection patterns
    CandlePattern.HAMMER: "crt",
    CandlePattern.ORB_LONG: "orb",
    CandlePattern.ORB_SHORT: "orb",
    CandlePattern.BOS_LONG: "bos",
    CandlePattern.BOS_SHORT: "bos",
    CandlePattern.FVG_LONG: "fvg",
    CandlePattern.FVG_SHORT: "fvg",
    CandlePattern.UNKNOWN: "unk",
}


def _tf_code(interval: str) -> str:
    """"15min" -> "15", "1h" -> "1h", "" -> "?"."""
    s = interval.strip().lower()
    if not s:
        return "?"
    return s[:-3] if s.endswith("min") else s


def build_trade_comment(pattern: CandlePattern, htf_interval: str, ltf_interval: str) -> str:
    strategy = _PATTERN_TO_STRATEGY.get(pattern, "unk")
    comment = f"apex-{strategy}-{_tf_code(htf_interval)}-{_tf_code(ltf_interval)}"
    return comment[:MT5_COMMENT_MAX_LEN]
