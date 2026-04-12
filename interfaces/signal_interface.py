"""
Inbound signal types — mirror of the Signal Engine's TradeSignal payload.

These dataclasses represent the data that arrives over the WebSocket from
the Signal Engine.  They are the boundary types: nothing outside the
`signals/` package should depend on raw dicts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
from utils.symbol_utils import normalise_symbol


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


class BosDirection(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"


class CandlePattern(str, Enum):
    SHOOTING_STAR = "SHOOTING_STAR"
    HAMMER = "HAMMER"
    CRT_BUY = "CRT_BUY"
    CRT_SELL = "CRT_SELL"


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
        return cls(
            open=d["open"],
            high=d["high"],
            low=d["low"],
            close=d["close"],
            timestamp=d["timestamp"],
            wick_ratio=d["wickRatio"],
            pattern=CandlePattern(d["pattern"]),
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
    ltf_range: LtfRange
    rejection_candle: RejectionCandle
    created_at: int
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

    @classmethod
    def from_dict(cls, d: dict) -> InboundSignal:
        return cls(
            id=d["id"],
            symbol=normalise_symbol(d["symbol"]),
            direction=SignalDirection(d["direction"]),
            status=SignalStatus(d["status"]),
            entry_price=d["entryPrice"],
            stop_loss=d["stopLoss"],
            tp1=d["tp1"],
            tp2=d["tp2"],
            risk_reward_ratio=d["riskRewardRatio"],
            risk_pips=d["riskPips"],
            htf_range=HtfRange.from_dict(d["htfRange"]),
            ltf_range=LtfRange.from_dict(d["ltfRange"]),
            rejection_candle=RejectionCandle.from_dict(d["rejectionCandle"]),
            created_at=d["createdAt"],
            pending_at=d.get("pendingAt"),
            triggered_at=d.get("triggeredAt"),
            tp1_hit_at=d.get("tp1HitAt"),
            tp2_hit_at=d.get("tp2HitAt"),
            sl_hit_at=d.get("slHitAt"),
            invalidated_at=d.get("invalidatedAt"),
            expired_at=d.get("expiredAt"),
            closed_at=d.get("closedAt"),
            outcome=d.get("outcome"),
            realized_rr=d.get("realizedRR"),
            close_price=d.get("closePrice"),
        )
