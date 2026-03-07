"""
Converts a TradePlan into a broker order and executes it via MT5.
"""

from __future__ import annotations

import logging

from brokers.mt5.mt5_orders import Mt5Orders
from brokers.mt5.mt5_types import Mt5OrderType
from brokers.mt5.mt5_positions import Mt5Positions
from config.config import ExecutionConfig
from infrastructure.metrics import metrics
from interfaces.trade import OrderSide, TradePlan

logger = logging.getLogger(__name__)


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

    def execute_market_order(self, plan: TradePlan) -> tuple[int, float]:
        """
        Submit a market order for *plan*.

        Returns (ticket, executed_price).
        Raises on broker rejection.
        """
        order_type = (
            Mt5OrderType.BUY if plan.side == OrderSide.BUY else Mt5OrderType.SELL
        )

        # Use the current ask/bid as the requested price
        tick = self._positions.get_current_tick(plan.symbol)
        if tick is None:
            raise RuntimeError(f"Cannot get current tick for {plan.symbol}")

        price = tick.ask if plan.side == OrderSide.BUY else tick.bid

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
        )

        metrics.increment("orders.filled")
        return result.ticket, result.executed_price
