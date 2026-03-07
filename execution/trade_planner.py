"""
Converts a validated, risk-approved signal into a concrete TradePlan.

Live-account adjustment:
  [2] Spread surcharge — the real cost of entering a trade includes the spread
      paid on entry.  If SPREAD_RISK_MULTIPLIER > 0, the spread (in price units)
      is added to the SL distance before sizing, so the lot size already accounts
      for the wider effective risk on a live account.

      Example:  entry=1.08450, SL=1.08200, spread=0.00020 (2 pips), multiplier=1.0
                raw_sl_distance = 0.00250
                adjusted        = 0.00250 + 1.0 × 0.00020 = 0.00270
                Result: slightly smaller lot size — you risk the same $ amount
                        even after paying the spread.

      Set SPREAD_RISK_MULTIPLIER=0.0 to disable (demo / tight ECN accounts).
"""

from __future__ import annotations

import logging
import math

from config.config import RiskConfig, ExecutionConfig
from interfaces.position import AccountInfo, SymbolInfo
from interfaces.signal_interface import InboundSignal, SignalDirection
from interfaces.trade import OrderSide, TradePlan
from utils.lot_calculator import calculate_lot_size, RiskMode
from utils.price_utils import pip_size
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

        # ── [2] Spread-adjusted stop loss distance ─────────────────────────
        spread_price = (
            (symbol_info.ask - symbol_info.bid)
            if symbol_info.ask and symbol_info.bid
            else 0.0
        )
        spread_surcharge = spread_price * self._exec.spread_risk_multiplier
        raw_sl_distance = abs(signal.entry_price - signal.stop_loss)
        adjusted_sl_distance = raw_sl_distance + spread_surcharge

        if signal.direction == SignalDirection.LONG:
            sizing_sl = signal.entry_price - adjusted_sl_distance
        else:
            sizing_sl = signal.entry_price + adjusted_sl_distance

        pip = pip_size(symbol_info.point, symbol_info.digits)

        if spread_surcharge > 0:
            logger.info(
                "Spread surcharge applied to lot sizing",
                extra={
                    "symbol": signal.symbol,
                    "spread_pips": round(spread_price / pip, 1),
                    "surcharge_pips": round(spread_surcharge / pip, 1),
                    "raw_sl_pips": round(raw_sl_distance / pip, 1),
                    "adjusted_sl_pips": round(adjusted_sl_distance / pip, 1),
                },
            )

        # ── Lot size calculation ───────────────────────────────────────────
        calc = calculate_lot_size(
            account_balance=account_info.balance,
            risk_mode=RiskMode(self._risk.risk_mode),
            risk_percent=self._risk.risk_percent_per_trade,
            risk_fixed=self._risk.risk_fixed_amount,
            entry_price=signal.entry_price,
            stop_loss=sizing_sl,
            symbol_info=symbol_info,
            max_lot=self._risk.max_lot_size,
            min_lot=self._risk.min_lot_size,
        )

        # ── TP1 / TP2 lot split ───────────────────────────────────────────
        tp1_frac = self._exec.tp1_partial_close_percent / 100.0
        tp1_lot = _floor_to_step(calc.lot_size * tp1_frac, symbol_info.lot_step)
        tp2_lot = _floor_to_step(calc.lot_size - tp1_lot, symbol_info.lot_step)
        risk_pct = (
            (calc.risk_amount / account_info.balance) * 100.0
            if account_info.balance
            else 0.0
        )

        plan = TradePlan(
            signal_id=signal.id,
            symbol=signal.symbol,
            side=side,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,  # real SL on broker — not sizing SL
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
                "risk_mode": calc.risk_mode.value,
                "lot_size": calc.lot_size,
                "tp1_lots": tp1_lot,
                "tp2_lots": tp2_lot,
                "risk_amount": round(calc.risk_amount, 2),
                "risk_pct": round(risk_pct, 2),
                "spread_pips": round(spread_price / pip, 1) if spread_price else 0,
            },
        )
        return plan


def _floor_to_step(value: float, step: float) -> float:
    return round(math.floor(value / step) * step, 2)
