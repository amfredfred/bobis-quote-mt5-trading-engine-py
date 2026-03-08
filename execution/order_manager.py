"""
Converts a TradePlan into a broker order and executes it via MT5.

Live-account protections:
  [1] Post-fill slippage validation — close immediately if exceeded
  [3] Retry on requote / rejection with fresh price each attempt
  [4] Partial fill detection — returns actual filled volume
"""

from __future__ import annotations

import logging
import time

from brokers.mt5.mt5_orders import Mt5Orders
from brokers.mt5.mt5_positions import Mt5Positions
from brokers.mt5.mt5_types import Mt5OrderType
from config.config import ExecutionConfig
from infrastructure.metrics import metrics
from interfaces.position import SymbolInfo
from interfaces.trade import OrderSide, TradePlan
from utils.price_utils import pip_size

logger = logging.getLogger(__name__)

# Retcodes worth retrying (transient broker refusals)
_RETRYABLE_RETCODES = {
    10004,  # TRADE_RETCODE_REQUOTE
    10006,  # TRADE_RETCODE_REJECT
    10007,  # TRADE_RETCODE_CANCEL
    10018,  # TRADE_RETCODE_MARKET_CLOSED
    10016,  # TRADE_RETCODE_INVALID_STOPS
}


class OrderManager:
    def __init__(
        self,
        mt5_orders: Mt5Orders,
        mt5_positions: Mt5Positions,
        exec_config: ExecutionConfig,
    ) -> None:
        self._orders = mt5_orders
        self._positions = mt5_positions
        self._cfg = exec_config

    def execute_market_order(
        self,
        plan: TradePlan,
        symbol_info: SymbolInfo,
    ) -> tuple[int, float, float]:
        """
        Submit a market order for *plan*.

        Returns (ticket, executed_price, filled_volume).
        filled_volume may differ from plan.lot_size on a live broker.

        Raises on:
          - All retries exhausted
          - Post-fill slippage exceeding MAX_ENTRY_SLIPPAGE_PIPS
        """
        order_type = (
            Mt5OrderType.BUY if plan.side == OrderSide.BUY else Mt5OrderType.SELL
        )

        pip = pip_size(symbol_info.point, symbol_info.digits)
        last_error: Exception | None = None
        max_attempts = 1 + self._cfg.order_retry_count

        for attempt in range(1, max_attempts + 1):

            # Fresh price on every attempt
            tick = self._positions.get_current_tick(plan.symbol)
            if tick is None:
                raise RuntimeError(f"Cannot get current tick for {plan.symbol}")
            price = tick.ask if plan.side == OrderSide.BUY else tick.bid

            try:
                result = self._orders.open_market_order(
                    symbol=plan.symbol,
                    order_type=order_type,
                    volume=plan.lot_size,
                    price=price,
                    sl=plan.stop_loss,
                    tp=plan.tp2,
                    slippage=self._cfg.slippage,
                    magic=self._cfg.magic,
                    comment=self._cfg.comment,
                    filling_mode=symbol_info.order_filling_mode,
                )
            except RuntimeError as exc:
                retcode = _extract_retcode(exc)
                if retcode in _RETRYABLE_RETCODES and attempt < max_attempts:
                    # [3] Requote / rejection — retry with fresh price
                    logger.warning(
                        "Order retryable error — retrying",
                        extra={
                            "attempt": attempt,
                            "max": max_attempts,
                            "retcode": retcode,
                            "symbol": plan.symbol,
                        },
                    )
                    metrics.increment("orders.retried")
                    time.sleep(self._cfg.order_retry_delay_sec)
                    last_error = exc
                    continue
                raise

            # ── Order accepted ────────────────────────────────────────────

            # [4] Partial fill check
            filled_volume = result.volume
            if filled_volume < plan.lot_size:
                logger.warning(
                    "Partial fill detected",
                    extra={
                        "ticket": result.ticket,
                        "symbol": plan.symbol,
                        "requested_lots": plan.lot_size,
                        "filled_lots": filled_volume,
                        "shortfall_lots": round(plan.lot_size - filled_volume, 2),
                    },
                )
                metrics.increment("orders.partial_fills")

            # [1] Post-fill slippage validation using broker pip size
            slippage_pips = abs(result.executed_price - plan.entry_price) / pip

            if slippage_pips > self._cfg.max_entry_slippage_pips:
                logger.error(
                    "Fill slippage exceeds limit — closing position",
                    extra={
                        "ticket": result.ticket,
                        "symbol": plan.symbol,
                        "planned_entry": plan.entry_price,
                        "executed_price": result.executed_price,
                        "slippage_pips": round(slippage_pips, 1),
                        "max_pips": self._cfg.max_entry_slippage_pips,
                        "digits": symbol_info.digits,
                        "point": symbol_info.point,
                    },
                )
                metrics.increment("orders.slippage_exceeded")
                self._emergency_close(
                    result.ticket, plan, order_type, result.executed_price
                )
                raise RuntimeError(
                    f"Entry slippage {slippage_pips:.1f} pips exceeds limit "
                    f"{self._cfg.max_entry_slippage_pips} pips — position closed"
                )

            if slippage_pips > 0:
                logger.info(
                    "Fill slippage within limit",
                    extra={
                        "symbol": plan.symbol,
                        "slippage_pips": round(slippage_pips, 1),
                        "max_pips": self._cfg.max_entry_slippage_pips,
                        "direction": (
                            "better"
                            if _is_better_price(
                                plan.side, result.executed_price, plan.entry_price
                            )
                            else "worse"
                        ),
                    },
                )

            metrics.increment("orders.filled")
            return result.ticket, result.executed_price, filled_volume

        raise last_error or RuntimeError("Order failed after all retries")

    # ── Emergency close ───────────────────────────────────────────────────

    def _emergency_close(
        self,
        ticket: int,
        plan: TradePlan,
        order_type: int,
        price: float,
    ) -> None:
        try:
            tick = self._positions.get_current_tick(plan.symbol)
            close_price = (
                (tick.bid if plan.side == OrderSide.BUY else tick.ask)
                if tick
                else price
            )
            self._orders.close_position(
                ticket=ticket,
                symbol=plan.symbol,
                side=order_type,
                volume=plan.lot_size,
                price=close_price,
                slippage=self._cfg.slippage,
                magic=self._cfg.magic,
                comment=f"slippage-close {self._cfg.comment}",
            )
            logger.info("Emergency close executed", extra={"ticket": ticket})
            metrics.increment("orders.emergency_closes")
        except Exception:
            logger.exception(
                "Emergency close FAILED — manual intervention required",
                extra={"ticket": ticket, "symbol": plan.symbol},
            )


def _extract_retcode(exc: RuntimeError) -> int:
    for part in str(exc).split():
        part = part.rstrip(")")
        if part.startswith("retcode="):
            try:
                return int(part.split("=")[1])
            except ValueError:
                pass
    return -1


def _is_better_price(side: OrderSide, executed: float, planned: float) -> bool:
    if side == OrderSide.BUY:
        return executed < planned
    return executed > planned
