"""Broker-side position and account types."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class PositionSide(str, Enum):
    BUY  = "BUY"
    SELL = "SELL"


@dataclass(frozen=True)
class Position:
    """A live position as reported by MT5."""
    ticket:        int
    symbol:        str
    side:          PositionSide
    lots:          float
    open_price:    float
    current_price: float
    stop_loss:     float
    take_profit:   float
    swap:          float
    commission:    float
    profit:        float
    open_time:     int       # Unix ms
    comment:       str
    magic:         int


@dataclass(frozen=True)
class AccountInfo:
    login:        int
    server:       str
    currency:     str
    balance:      float
    equity:       float
    margin:       float
    free_margin:  float
    margin_level: float
    leverage:     int


@dataclass(frozen=True)
class SymbolInfo:
    symbol:        str
    digits:        int
    point:         float
    tick_size:     float
    tick_value:    float
    contract_size: float
    lot_min:       float
    lot_max:       float
    lot_step:      float
    spread:        int
    ask:           float
    bid:           float
