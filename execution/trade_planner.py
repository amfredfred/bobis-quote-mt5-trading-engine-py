"""
Converts a validated, risk-approved signal into a concrete TradePlan.
"""

from __future__ import annotations

import logging
import math

from config.config import RiskConfig, ExecutionConfig
from interfaces.position import AccountInfo, SymbolInfo
from interfaces.signal_interface import InboundSignal, SignalDirection
from interfaces.trade import OrderSide, TradePlan
from utils.lot_calculator import RiskMode, calculate_lot_size
from utils.time_utils import now_ms

logger = logging.getLogger(__name__)


class TradePlanner:
    def __init__(self, risk_config: RiskConfig, exec_config: ExecutionConfig) -> None:
        self._risk = risk_config
        self._exec = exec_config

    def plan(
        self,
        signal: InboundSignal,
        account_info: AccountInfo,
        symbol_info: SymbolInfo,
    ) -> TradePlan:
        side = (
            OrderSide.BUY
            if signal.direction == SignalDirection.LONG
            else OrderSide.SELL
        )

        calc = calculate_lot_size(
            account_balance=account_info.balance,
            risk_mode=self._risk.risk_mode,
            risk_percent=self._risk.risk_percent_per_trade,
            risk_fixed=self._risk.risk_fixed_amount,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            symbol_info=symbol_info,
            max_lot=self._risk.max_lot_size,
            min_lot=self._risk.min_lot_size,
        )

        tp1_frac = self._exec.tp1_partial_close_percent / 100.0
        tp1_lot = _floor_to_step(calc.lot_size * tp1_frac, symbol_info.lot_step)
        tp2_lot = _floor_to_step(calc.lot_size - tp1_lot, symbol_info.lot_step)
        risk_pct = (
            (calc.risk_amount / account_info.balance * 100.0)
            if account_info.balance
            else 0.0
        )

        plan = TradePlan(
            signal_id=signal.id,
            symbol=signal.symbol,
            side=side,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            tp1=signal.tp1,
            tp2=signal.tp2,
            lot_size=calc.lot_size,
            tp1_lot_size=tp1_lot,
            tp2_lot_size=tp2_lot,
            risk_amount=calc.risk_amount,
            risk_percent=risk_pct,
            risk_reward_ratio=signal.risk_reward_ratio,
            planned_at=now_ms(),
            signal=signal,
        )

        logger.info(
            "Trade planned",
            extra={
                "signal_id": signal.id,
                "symbol": signal.symbol,
                "side": side.value,
                "risk_mode": self._risk.risk_mode.value,
                "risk_amount": round(calc.risk_amount, 2),
                "risk_pct": round(risk_pct, 2),
                "lot_size": calc.lot_size,
                "tp1_lots": tp1_lot,
                "tp2_lots": tp2_lot,
            },
        )
        return plan


def _floor_to_step(value: float, step: float) -> float:
    return round(math.floor(value / step) * step, 2)
