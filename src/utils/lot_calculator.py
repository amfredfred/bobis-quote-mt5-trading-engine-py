"""
Position-size calculator.

Risk amount is pre-computed by the caller (LossTracker.daily_risk_amount)
and passed in directly. The lot formula:

    lot_size = risk_amount / loss_per_lot

loss_per_lot (dollar cost of 1.0 lot moving from entry to stop) should
normally come from Mt5Positions.calc_loss_per_lot (MT5's own
order_calc_profit — correct for every trade_calc_mode). When that isn't
available (offline/backtest context, or the MT5 call failed), this falls
back to the tick_value/tick_size forex formula:

    risk_pips  = |entry − stop_loss| / pip_size
    pip_value  = (tick_value / tick_size) × pip_size   (per lot)
    loss_per_lot = risk_pips × pip_value

That fallback is only valid for SYMBOL_CALC_MODE_FOREX/CFD-style
instruments — it silently under-costs CFDINDEX symbols (e.g. Deriv's
synthetic indices), which don't use tick_value/tick_size at all. See the
loss_per_lot docstring on calc_loss_per_lot for the incident this
fallback exists to avoid depending on by default.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from src.domain.position import SymbolInfo
from src.utils.price import pip_size, normalise_lots

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LotCalcResult:
    lot_size: float
    risk_amount: float  # actual currency amount being risked
    risk_pips: float


def calculate_lot_size(
    risk_amount: float,   # pre-computed by LossTracker.daily_risk_amount()
    entry_price: float,
    stop_loss: float,
    symbol_info: SymbolInfo,
    max_lot: float,
    min_lot: float,
    loss_per_lot: float | None = None,  # from Mt5Positions.calc_loss_per_lot
) -> LotCalcResult:

    pip = pip_size(symbol_info.point, symbol_info.digits)
    risk_pips = abs(entry_price - stop_loss) / pip

    if risk_pips == 0:
        logger.error("lot_calculator: risk_pips is 0 — cannot size position")
        return LotCalcResult(
            lot_size=min_lot,
            risk_amount=risk_amount,
            risk_pips=0.0,
        )

    if loss_per_lot is not None and loss_per_lot > 0:
        # Preferred path: MT5's own order_calc_profit, correct for every
        # trade_calc_mode (forex, CFD, CFDINDEX, ...).
        pass
    else:
        # Fallback: tick_value/tick_size forex formula — only valid for
        # SYMBOL_CALC_MODE_FOREX/CFD instruments. See module docstring.
        if symbol_info.tick_size == 0:
            logger.error("lot_calculator: tick_size is 0 — symbol info incomplete")
            return LotCalcResult(
                lot_size=min_lot,
                risk_amount=risk_amount,
                risk_pips=risk_pips,
            )

        pip_value = (symbol_info.tick_value / symbol_info.tick_size) * pip

        if pip_value == 0:
            logger.error("lot_calculator: pip_value is 0")
            return LotCalcResult(
                lot_size=min_lot,
                risk_amount=risk_amount,
                risk_pips=risk_pips,
            )

        loss_per_lot = risk_pips * pip_value

    # ── Calculate and normalise lots ───────────────────────────────────────
    raw_lots = risk_amount / loss_per_lot
    lot_size = normalise_lots(
        raw_lots,
        symbol_info.lot_step,
        min_lot,
        min(max_lot, symbol_info.lot_max),
    )

    logger.debug(
        "lot_calculator result",
        extra={
            "symbol": symbol_info.symbol,
            "risk_amount": round(risk_amount, 2),
            "risk_pips": round(risk_pips, 1),
            "loss_per_lot": round(loss_per_lot, 6),
            "raw_lots": round(raw_lots, 4),
            "lot_size": lot_size,
        },
    )

    return LotCalcResult(
        lot_size=lot_size,
        risk_amount=risk_amount,
        risk_pips=risk_pips,
    )









