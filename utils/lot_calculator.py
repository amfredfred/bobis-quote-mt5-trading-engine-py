"""
Position-size calculator.

Supports two risk modes:

  PERCENTAGE  risk_amount = balance × risk_percent / 100
  FIXED       risk_amount = fixed dollar amount directly

Both modes then share the same lot formula:
    risk_pips  = |entry − stop_loss| / pip_size
    pip_value  = (tick_value / tick_size) × pip_size   (per lot)
    lot_size   = risk_amount / (risk_pips × pip_value)

Set RISK_MODE=percentage (default) or RISK_MODE=fixed in .env.
When RISK_MODE=fixed, set RISK_FIXED_AMOUNT to the dollar amount per trade.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from interfaces.position import SymbolInfo
from utils.price_utils import pip_size, normalise_lots

logger = logging.getLogger(__name__)


class RiskMode(str, Enum):
    PERCENTAGE = "percentage"
    FIXED = "fixed"


@dataclass(frozen=True)
class LotCalcResult:
    lot_size: float
    risk_amount: float  # actual currency amount being risked
    risk_pips: float
    risk_mode: RiskMode


def calculate_lot_size(
    account_balance: float,
    risk_mode: RiskMode,
    risk_percent: float,  # used when mode=PERCENTAGE
    risk_fixed: float,  # used when mode=FIXED  (e.g. 100.0 USD)
    entry_price: float,
    stop_loss: float,
    symbol_info: SymbolInfo,
    max_lot: float,
    min_lot: float,
) -> LotCalcResult:

    # ── Step 1: resolve risk amount ───────────────────────────────────────
    if risk_mode == RiskMode.FIXED:
        risk_amount = risk_fixed
    else:
        risk_amount = account_balance * (risk_percent / 100.0)

    # ── Step 2: pip distance to stop ──────────────────────────────────────
    pip = pip_size(symbol_info.symbol)
    risk_pips = abs(entry_price - stop_loss) / pip

    if risk_pips == 0:
        logger.error("lot_calculator: risk_pips is 0 — cannot size position")
        return LotCalcResult(
            lot_size=min_lot,
            risk_amount=risk_amount,
            risk_pips=0.0,
            risk_mode=risk_mode,
        )

    # ── Step 3: pip value per lot ─────────────────────────────────────────
    if symbol_info.tick_size == 0:
        logger.error("lot_calculator: tick_size is 0 — symbol info incomplete")
        return LotCalcResult(
            lot_size=min_lot,
            risk_amount=risk_amount,
            risk_pips=risk_pips,
            risk_mode=risk_mode,
        )

    pip_value = (symbol_info.tick_value / symbol_info.tick_size) * pip

    if pip_value == 0:
        logger.error("lot_calculator: pip_value is 0")
        return LotCalcResult(
            lot_size=min_lot,
            risk_amount=risk_amount,
            risk_pips=risk_pips,
            risk_mode=risk_mode,
        )

    # ── Step 4: calculate and normalise lots ──────────────────────────────
    raw_lots = risk_amount / (risk_pips * pip_value)
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
            "risk_mode": risk_mode.value,
            "risk_amount": round(risk_amount, 2),
            "risk_pips": round(risk_pips, 1),
            "pip_value": round(pip_value, 6),
            "raw_lots": round(raw_lots, 4),
            "lot_size": lot_size,
        },
    )

    return LotCalcResult(
        lot_size=lot_size,
        risk_amount=risk_amount,
        risk_pips=risk_pips,
        risk_mode=risk_mode,
    )
