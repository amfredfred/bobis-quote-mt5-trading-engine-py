"""
Inbound signal types — mirror of the Signal Engine's TradeSignal payload.

These dataclasses represent the data that arrives over the WebSocket from
the Signal Engine.  They are the boundary types: nothing outside the
`signals/` package should depend on raw dicts.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional
from src.utils.symbol import normalise_symbol
from src.utils.time import now_ms

logger = logging.getLogger(__name__)


def _parse_enum_lenient(enum_cls, raw, fallback):
    """Parse `raw` as `enum_cls`, falling back (with a logged warning)
    instead of raising when Signal Engine emits a value this codebase's
    mirrored enum doesn't know about yet. See the 2026-07-19/20 incident:
    BOS_LONG/BOS_SHORT/FVG_SHORT signals were silently dropped entirely
    because CandlePattern here hadn't been updated to match Signal Engine's
    domain/entities/enums.py — never again fatal for a single unrecognized
    value on a field nothing downstream reads for execution decisions."""
    try:
        return enum_cls(raw)
    except ValueError:
        logger.warning(
            "Unrecognized %s value %r — update %s in signal_interface.py "
            "to match Signal Engine's domain/entities/enums.py. Falling "
            "back to %s.",
            enum_cls.__name__, raw, enum_cls.__name__, fallback.name,
        )
        return fallback


# ── Enums ──────────────────────────────────────────────────────────────────────


class SignalDirection(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class SignalStatus(str, Enum):
    PENDING = "PENDING"
    TRIGGERED = "TRIGGERED"
    TP1_HIT = "TP1_HIT"
    TP2_HIT = "TP2_HIT"
    SL_HIT = "SL_HIT"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"
    # Same-bar SL/TP ambiguity, unresolvable from OHLC alone — Signal
    # Engine's own genuine status, reachable live (see signal_service.py's
    # UNDETERMINED -> SIGNAL_UNDETERMINED event mapping), not just backtest.
    UNDETERMINED = "UNDETERMINED"
    # Fallback for any status value this enum doesn't (yet) know about —
    # see InboundSignal.from_dict. Not currently read anywhere for
    # execution decisions (that's driven by this codebase's own separate
    # TradeStatus), so a permissive fallback here is safe.
    UNKNOWN = "UNKNOWN"


class BosDirection(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"


class CandlePattern(str, Enum):
    """Mirror of the Signal Engine's own CandlePattern (domain/entities/
    enums.py) — one value per strategy plugin's trigger pattern. This is a
    SEPARATE codebase from Signal Engine, so nothing enforces these two
    enums staying in sync; when Signal Engine adds a strategy/pattern, this
    one needs a matching update or every signal using it fails to
    deserialize (see UNKNOWN below for why that's now a warning, not a
    dropped signal, regardless of whether this list is kept current)."""

    SHOOTING_STAR = "SHOOTING_STAR"
    HAMMER = "HAMMER"
    CRT_BUY = "CRT_BUY"
    CRT_SELL = "CRT_SELL"
    ORB_LONG = "ORB_LONG"
    ORB_SHORT = "ORB_SHORT"
    BOS_LONG = "BOS_LONG"
    BOS_SHORT = "BOS_SHORT"
    FVG_LONG = "FVG_LONG"
    FVG_SHORT = "FVG_SHORT"
    # Fallback for any pattern value this enum doesn't (yet) know about —
    # see RejectionCandle.from_dict. Never raised as a deserialization
    # error; the raw string is logged instead so the signal still executes.
    UNKNOWN = "UNKNOWN"


class SignalEventName(str, Enum):
    PENDING = "signal.pending"
    TRIGGERED = "signal.triggered"
    TP1_HIT = "signal.tp1_hit"
    TP2_HIT = "signal.tp2_hit"
    SL_HIT = "signal.sl_hit"
    INVALIDATED = "signal.invalidated"
    EXPIRED = "signal.expired"
    UPDATED = "signal.updated"


# ── Sub-structures ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class HtfRange:
    range_high: float
    range_low: float
    bos_direction: BosDirection
    timestamp: int
    broken_at: int
    tp_level: float
    midpoint: float
    height: float
    htf_candle_open: int
    htf_candle_close: int

    @classmethod
    def from_dict(cls, d: dict) -> HtfRange:
        return cls(
            range_high=d["rangeHigh"],
            range_low=d["rangeLow"],
            bos_direction=BosDirection(d["bosDirection"]),
            timestamp=d["timestamp"],
            broken_at=d.get("brokenAt") or 0,
            tp_level=d.get("tpLevel") or 0.0,
            midpoint=d.get("midpoint") or (d["rangeHigh"] + d["rangeLow"]) / 2,
            height=d.get("height") or (d["rangeHigh"] - d["rangeLow"]),
            htf_candle_open=d.get("htfCandleOpen") or 0,
            htf_candle_close=d.get("htfCandleClose") or 0,
        )


@dataclass(frozen=True)
class LtfRange:
    range_high: float
    range_low: float
    timestamp: int
    direction: SignalDirection
    sl_level: float

    @classmethod
    def from_dict(cls, d: dict) -> LtfRange:
        return cls(
            range_high=d["rangeHigh"],
            range_low=d["rangeLow"],
            timestamp=d["timestamp"],
            direction=SignalDirection(d["direction"]),
            sl_level=d["slLevel"],
        )


@dataclass(frozen=True)
class RejectionCandle:
    open: float
    high: float
    low: float
    close: float
    timestamp: int
    wick_ratio: float
    pattern: CandlePattern
    wick_tip: float

    @classmethod
    def from_dict(cls, d: dict) -> RejectionCandle:
        # `pattern` is purely descriptive — nothing downstream reads it for
        # execution decisions (entry/SL/TP/direction all come from their
        # own fields) — so an unrecognized value falls back leniently
        # rather than dropping the whole signal.
        pattern = _parse_enum_lenient(CandlePattern, d["pattern"], CandlePattern.UNKNOWN)
        return cls(
            open=d["open"],
            high=d["high"],
            low=d["low"],
            close=d["close"],
            timestamp=d["timestamp"],
            wick_ratio=d["wickRatio"],
            pattern=pattern,
            wick_tip=d["wickTip"],
        )


# ── Top-level signal ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class InboundSignal:
    """
    A fully-deserialised signal from the Signal Engine.
    Immutable so it can safely be passed between components.
    """

    id: str
    symbol: str
    direction: SignalDirection
    status: SignalStatus
    entry_price: float
    stop_loss: float
    tp1: float
    tp2: float
    risk_reward_ratio: float
    risk_pips: float
    htf_range: HtfRange
    rejection_candle: RejectionCandle
    created_at: int
    htf_interval: str = ""
    ltf_interval: str = ""
    ltf_range: Optional[LtfRange] = None
    pending_at: Optional[int] = None
    triggered_at: Optional[int] = None
    tp1_hit_at: Optional[int] = None
    tp2_hit_at: Optional[int] = None
    sl_hit_at: Optional[int] = None
    invalidated_at: Optional[int] = None
    expired_at: Optional[int] = None
    closed_at: Optional[int] = None
    outcome: Optional[str] = None
    realized_rr: Optional[float] = None
    close_price: Optional[float] = None
    resolved_symbol: Optional[str] = None  # broker-resolved symbol, set at consumer level
    setup_candle_open_at: Optional[int] = None
    setup_candle_close_at: Optional[int] = None
    detected_at: Optional[int] = None
    emitted_at: Optional[int] = None
    received_at: Optional[int] = None
    queued_at: Optional[int] = None
    execution_started_at: Optional[int] = None
    order_sent_at: Optional[int] = None
    order_filled_at: Optional[int] = None
    trade_opened_at: Optional[int] = None

    @classmethod
    def from_dict(cls, d: dict) -> InboundSignal:
        received_at = _optional_int(d, "receivedAt", "received_at") or now_ms()
        emitted_at = _optional_int(d, "emittedAt", "emitted_at") or received_at
        detected_at = (
            _optional_int(d, "detectedAt", "detected_at", "triggeredAt", "triggered_at")
            or emitted_at
        )
        rejection = d.get("rejectionCandle") or d.get("rejection_candle") or {}
        setup_candle_open_at = (
            _optional_int(d, "setupCandleOpenAt", "setup_candle_open_at")
            or _optional_int(rejection, "timestamp")
        )
        setup_candle_close_at = _optional_int(
            d,
            "setupCandleCloseAt",
            "setup_candle_close_at",
            "rejectionCandleCloseAt",
            "rejection_candle_close_at",
        ) or _optional_int(rejection, "closeAt", "close_at")
        triggered_at = _optional_int(d, "triggeredAt", "triggered_at")
        if setup_candle_close_at is None:
            setup_candle_close_at = triggered_at
        return cls(
            id=d["id"],
            symbol=normalise_symbol(d["symbol"]),
            direction=SignalDirection(d["direction"]),
            status=_parse_enum_lenient(SignalStatus, d["status"], SignalStatus.UNKNOWN),
            entry_price=d["entryPrice"],
            stop_loss=d["stopLoss"],
            tp1=d["tp1"],
            tp2=d["tp2"],
            risk_reward_ratio=d["riskRewardRatio"],
            risk_pips=d["riskPips"],
            htf_range=HtfRange.from_dict(d["htfRange"]),
            rejection_candle=RejectionCandle.from_dict(d["rejectionCandle"]),
            created_at=d["createdAt"],
            htf_interval=str(d.get("htfInterval") or d.get("htf_interval") or ""),
            ltf_interval=str(d.get("ltfInterval") or d.get("ltf_interval") or ""),
            ltf_range=LtfRange.from_dict(d["ltfRange"]) if d.get("ltfRange") else None,
            pending_at=d.get("pendingAt"),
            triggered_at=triggered_at,
            tp1_hit_at=d.get("tp1HitAt"),
            tp2_hit_at=d.get("tp2HitAt"),
            sl_hit_at=d.get("slHitAt"),
            invalidated_at=d.get("invalidatedAt"),
            expired_at=d.get("expiredAt"),
            closed_at=d.get("closedAt"),
            outcome=d.get("outcome"),
            realized_rr=d.get("realizedRR"),
            close_price=d.get("closePrice"),
            setup_candle_open_at=setup_candle_open_at,
            setup_candle_close_at=setup_candle_close_at,
            detected_at=detected_at,
            emitted_at=emitted_at,
            received_at=received_at,
            queued_at=_optional_int(d, "queuedAt", "queued_at"),
            execution_started_at=_optional_int(d, "executionStartedAt", "execution_started_at"),
            order_sent_at=_optional_int(d, "orderSentAt", "order_sent_at"),
            order_filled_at=_optional_int(d, "orderFilledAt", "order_filled_at"),
            trade_opened_at=_optional_int(d, "tradeOpenedAt", "trade_opened_at"),
        )


def _optional_int(d: dict, *keys: str) -> Optional[int]:
    for key in keys:
        value = d.get(key)
        if value is None:
            continue
        return int(value)
    return None




