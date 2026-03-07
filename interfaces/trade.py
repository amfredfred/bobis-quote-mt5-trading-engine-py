"""Internal trade representation — created by the execution engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .signal_interface import InboundSignal


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class TradeStatus(str, Enum):
    PLANNED = "PLANNED"
    OPEN = "OPEN"
    PARTIALLY_CLOSED = "PARTIALLY_CLOSED"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"
    ERROR = "ERROR"


class CloseReason(str, Enum):
    TP1_HIT = "TP1_HIT"
    TP2_HIT = "TP2_HIT"
    SL_HIT = "SL_HIT"
    MANUAL = "MANUAL"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"
    ERROR = "ERROR"


@dataclass
class TradePlan:
    signal_id: str
    symbol: str
    side: OrderSide
    entry_price: float
    stop_loss: float
    tp1: float
    tp2: float
    lot_size: float
    tp1_lot_size: float  # lots to close at TP1
    tp2_lot_size: float  # lots to close at TP2
    risk_amount: float  # in account currency
    risk_percent: float
    risk_reward_ratio: float
    planned_at: int
    signal: "InboundSignal"


@dataclass
class Trade:
    id: str
    signal_id: str
    symbol: str
    side: OrderSide
    status: TradeStatus
    plan: TradePlan

    entry_ticket: Optional[int] = None  # MT5 ticket
    entry_price: Optional[float] = None
    entry_lots: float = 0.0
    current_lots: float = 0.0

    stop_loss: float = 0.0
    tp1: float = 0.0
    tp2: float = 0.0

    tp1_hit: bool = False
    tp1_hit_at: Optional[int] = None
    tp2_hit: bool = False
    tp2_hit_at: Optional[int] = None
    sl_hit: bool = False
    sl_hit_at: Optional[int] = None

    opened_at: Optional[int] = None
    closed_at: Optional[int] = None
    close_reason: Optional[CloseReason] = None
    close_price: Optional[float] = None
    realized_pnl: Optional[float] = None
    realized_rr: Optional[float] = None

    created_at: int = 0
    updated_at: int = 0

    def to_dict(self) -> dict:
        """Serialise for JSON persistence."""
        return {
            "id": self.id,
            "signalId": self.signal_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "status": self.status.value,
            "entryTicket": self.entry_ticket,
            "entryPrice": self.entry_price,
            "entryLots": self.entry_lots,
            "currentLots": self.current_lots,
            "stopLoss": self.stop_loss,
            "tp1": self.tp1,
            "tp2": self.tp2,
            "tp1Hit": self.tp1_hit,
            "tp1HitAt": self.tp1_hit_at,
            "tp2Hit": self.tp2_hit,
            "tp2HitAt": self.tp2_hit_at,
            "slHit": self.sl_hit,
            "slHitAt": self.sl_hit_at,
            "openedAt": self.opened_at,
            "closedAt": self.closed_at,
            "closeReason": self.close_reason.value if self.close_reason else None,
            "closePrice": self.close_price,
            "realizedPnl": self.realized_pnl,
            "realizedRR": self.realized_rr,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            # flatten plan essentials for readability
            "plan": {
                "signalId": self.plan.signal_id,
                "lotSize": self.plan.lot_size,
                "tp1LotSize": self.plan.tp1_lot_size,
                "tp2LotSize": self.plan.tp2_lot_size,
                "riskAmount": self.plan.risk_amount,
                "riskPercent": self.plan.risk_percent,
                "riskRewardRatio": self.plan.risk_reward_ratio,
            },
        }
