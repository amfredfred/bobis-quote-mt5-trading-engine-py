"""
Orchestrates the full execution pipeline for a triggered signal:

    1. Fetch live account + symbol info from MT5
    2. Risk check
    3. Build TradePlan
    4. Execute order via OrderManager
    5. Persist Trade in PositionStore
    6. Emit TRADE_OPENED
"""

from __future__ import annotations

import logging
import uuid

from brokers.mt5.mt5_positions import Mt5Positions
from config.config import ExecutionConfig
from core.event_bus import EventBus
from core.events import Events
from .order_manager import OrderManager
from .trade_planner import TradePlanner
from infrastructure.metrics import metrics
from positions.position_store import PositionStore
from risk.risk_engine import RiskEngine
from interfaces.signal_interface import InboundSignal
from interfaces.trade import Trade, TradeStatus
from utils.time_utils import now_ms

logger = logging.getLogger(__name__)


class ExecutionEngine:
    def __init__(
        self,
        risk_engine: RiskEngine,
        trade_planner: TradePlanner,
        order_manager: OrderManager,
        mt5_positions: Mt5Positions,
        position_store: PositionStore,
        event_bus: EventBus,
        exec_config: ExecutionConfig,
    ) -> None:
        self._risk = risk_engine
        self._planner = trade_planner
        self._orders = order_manager
        self._mt5_positions = mt5_positions
        self._store = position_store
        self._bus = event_bus
        self._cfg = exec_config
        self._daily_loss_pct: float = 0.0

    def update_daily_loss(self, loss_pct: float) -> None:
        """Called by bootstrap when daily P&L is updated."""
        self._daily_loss_pct = loss_pct

    def execute(self, signal: InboundSignal) -> Trade | None:
        logger.info(
            "ExecutionEngine processing signal",
            extra={
                "signal_id": signal.id,
                "symbol": signal.symbol,
                "direction": signal.direction.value,
            },
        )

        # ── 1. Fetch broker state ──────────────────────────────────────────
        try:
            account_info = self._mt5_positions.get_account_info()
            symbol_info = self._mt5_positions.get_symbol_info(signal.symbol)
        except Exception:
            logger.exception("ExecutionEngine: failed to fetch broker state")
            self._bus.emit(
                Events.TRADE_ERROR, {"signal": signal, "reason": "broker_unavailable"}
            )
            return None

        # ── 2. Risk check ──────────────────────────────────────────────────
        open_trades = self._store.get_open_trades()
        decision = self._risk.evaluate(signal, open_trades, self._daily_loss_pct)

        if not decision.approved:
            self._bus.emit(
                Events.RISK_REJECTED, {"signal": signal, "reason": decision.reason}
            )
            return None

        self._bus.emit(Events.RISK_APPROVED, {"signal": signal})

        # ── 3. Plan trade ──────────────────────────────────────────────────
        try:
            plan = self._planner.plan(signal, account_info, symbol_info)
        except Exception:
            logger.exception("ExecutionEngine: trade planning failed")
            self._bus.emit(
                Events.TRADE_ERROR, {"signal": signal, "reason": "planning_failed"}
            )
            return None

        self._bus.emit(Events.TRADE_PLANNED, {"plan": plan})

        # ── 4. Execute order ───────────────────────────────────────────────
        try:
            ticket, executed_price = self._orders.execute_market_order(plan)
        except Exception as exc:
            logger.exception("ExecutionEngine: order execution failed")
            self._bus.emit(Events.TRADE_ERROR, {"signal": signal, "reason": str(exc)})
            metrics.increment("orders.rejected")
            return None

        # ── 5. Create Trade record ─────────────────────────────────────────
        ts = now_ms()
        trade = Trade(
            id=str(uuid.uuid4()),
            signal_id=signal.id,
            symbol=signal.symbol,
            side=plan.side,
            status=TradeStatus.OPEN,
            plan=plan,
            entry_ticket=ticket,
            entry_price=executed_price,
            entry_lots=plan.lot_size,
            current_lots=plan.lot_size,
            stop_loss=plan.stop_loss,
            tp1=plan.tp1,
            tp2=plan.tp2,
            opened_at=ts,
            created_at=ts,
            updated_at=ts,
        )

        self._store.add(trade)

        # ── 6. Emit ────────────────────────────────────────────────────────
        self._bus.emit(Events.TRADE_OPENED, trade)
        metrics.increment("trades.opened")
        metrics.set_gauge("trades.open_count", len(self._store.get_open_trades()))

        logger.info(
            "Trade opened",
            extra={
                "trade_id": trade.id,
                "signal_id": signal.id,
                "ticket": ticket,
                "entry_price": executed_price,
                "lots": plan.lot_size,
            },
        )
        return trade
