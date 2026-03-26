"""
Orchestrates the full execution pipeline for a triggered signal:

    1. Fetch live account + symbol info from MT5
    2. Risk check
    3. Build TradePlan
    4. Execute order via OrderManager
       - includes retry, slippage check, partial fill detection
    5. Recalculate TP1/TP2 lot split from ACTUAL filled volume  [4]
    6. Persist Trade in PositionStore + disk
    7. Emit TRADE_OPENED

Latency tracking  [5]:
    signal_to_trade_ms   — triggered_at → trade.opened_at  (full pipeline)
    broker_round_trip_ms — order sent → order confirmed     (MT5 only)
    Both are emitted as metrics gauges and appear in the monitoring dashboard.
"""

from __future__ import annotations

import logging
import math
import threading
import uuid

from brokers.mt5.mt5_positions import Mt5Positions
from config.config import ExecutionConfig
from core.event_bus import EventBus
from core.events import Events
from execution.order_manager import OrderManager
from execution.trade_planner import TradePlanner
from infrastructure.metrics import metrics
from positions.position_store import PositionStore
from risk.risk_engine import RiskEngine
from storage.trade_repository import TradeRepository
from interfaces.signal_interface import InboundSignal
from interfaces.trade import Trade, TradeStatus
from utils.price_utils import normalise_lots
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
        trade_repo: TradeRepository,
        event_bus: EventBus,
        exec_config: ExecutionConfig,
    ) -> None:
        self._risk = risk_engine
        self._planner = trade_planner
        self._orders = order_manager
        self._mt5_positions = mt5_positions
        self._store = position_store
        self._repo = trade_repo
        self._bus = event_bus
        self._cfg = exec_config
        self._pending: dict[str, int] = {}
        self._pending_lock = threading.Lock()
        self._daily_loss_pct: float = 0.0  # cached — refreshed by position manager poll

    def update_daily_loss(self, loss_pct: float) -> None:
        """Called by PositionManager on each poll cycle."""
        self._daily_loss_pct = loss_pct

    def _pending_total(self) -> int:
        return sum(self._pending.values())

    def _pending_for(self, symbol: str) -> int:
        return self._pending.get(symbol, 0)

    def _reserve(self, symbol: str) -> None:
        self._pending[symbol] = self._pending.get(symbol, 0) + 1

    def _release(self, symbol: str) -> None:
        self._pending[symbol] = max(0, self._pending.get(symbol, 0) - 1)

    def execute(self, signal: InboundSignal) -> Trade | None:
        # ── [5] Pipeline start time ────────────────────────────────────────
        pipeline_start_ms = now_ms()

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
        with self._pending_lock:
            open_trades = self._store.get_open_trades()
            effective_open = len(open_trades) + self._pending_total()
            effective_symbol = len(
                [t for t in open_trades if t.symbol == signal.symbol]
            ) + self._pending_for(signal.symbol)
            decision = self._risk.evaluate(
                signal,
                open_trades,
                self._daily_loss_pct,
                effective_open,
                effective_symbol,
                symbol_info,
            )

            if not decision.approved:
                self._bus.emit(
                    Events.RISK_REJECTED, {"signal": signal, "reason": decision.reason}
                )
                return None

            self._reserve(signal.symbol)

        self._bus.emit(Events.RISK_APPROVED, {"signal": signal})

        # ── 3. Plan trade ──────────────────────────────────────────────────
        try:
            plan = self._planner.plan(signal, account_info, symbol_info)
        except Exception:
            with self._pending_lock:
                self._release(signal.symbol)
            logger.exception("ExecutionEngine: trade planning failed")
            self._bus.emit(
                Events.TRADE_ERROR, {"signal": signal, "reason": "planning_failed"}
            )
            return None

        self._bus.emit(Events.TRADE_PLANNED, {"plan": plan})

        # ── 4. Execute order ───────────────────────────────────────────────
        broker_send_ms = now_ms()  # [5] broker round-trip start
        try:
            ticket, executed_price, filled_volume = self._orders.execute_market_order(
                plan, symbol_info
            )
        except Exception as exc:
            with self._pending_lock:
                self._release(signal.symbol)
            logger.exception("ExecutionEngine: order execution failed")
            self._bus.emit(Events.TRADE_ERROR, {"signal": signal, "reason": str(exc)})
            metrics.increment("orders.rejected")
            return None

        broker_round_trip_ms = now_ms() - broker_send_ms  # [5]

        # ── 5. Recalculate TP lots from actual filled volume  [4] ──────────
        # On a live broker, filled_volume may be less than plan.lot_size.
        # Recompute the TP1/TP2 split so position manager closes correct amounts.
        tp1_lot, tp2_lot = _split_lots(
            filled_volume,
            self._cfg.tp1_partial_close_percent,
            symbol_info.lot_step,
        )

        if filled_volume != plan.lot_size:
            logger.info(
                "Lot split recalculated from actual fill",
                extra={
                    "planned_lots": plan.lot_size,
                    "filled_lots": filled_volume,
                    "tp1_lots": tp1_lot,
                    "tp2_lots": tp2_lot,
                },
            )

        # ── 5b. Shift TP1, TP2 and SL to actual fill price ─────────────────
        # The signal's levels are calculated relative to signal.entry_price.
        # If the broker fills at a different price, all levels must shift by
        # the same amount so R:R and risk amount remain correct.
        #
        # Example (LONG, 23 pip adverse slippage):
        #   signal entry = 1.38296, fill = 1.38526  → slippage = +0.00230
        #   signal TP1   = 1.38436 → adjusted TP1 = 1.38666
        #   signal TP2   = 1.38576 → adjusted TP2 = 1.38806
        #   signal SL    = 1.38243 → adjusted SL  = 1.38473
        #
        # Without this adjustment TP1 sits 9 pips BELOW the actual entry,
        # triggering an immediate loss-close the moment price dips at all.
        fill_slippage = executed_price - plan.entry_price  # signed; +ve = worse for BUY
        adjusted_tp1 = plan.tp1        + fill_slippage
        adjusted_tp2 = plan.tp2        + fill_slippage
        adjusted_sl  = plan.stop_loss  + fill_slippage

        if abs(fill_slippage) > 1e-8:
            logger.info(
                "Plan levels shifted to actual fill price",
                extra={
                    "symbol": signal.symbol,
                    "signal_entry":  plan.entry_price,
                    "fill_price":    executed_price,
                    "fill_slippage": round(fill_slippage, 5),
                    "original_sl":   plan.stop_loss,
                    "adjusted_sl":   round(adjusted_sl, 5),
                    "original_tp1":  plan.tp1,
                    "adjusted_tp1":  round(adjusted_tp1, 5),
                    "original_tp2":  plan.tp2,
                    "adjusted_tp2":  round(adjusted_tp2, 5),
                },
            )

        from dataclasses import replace
        plan = replace(
            plan,
            lot_size=filled_volume,
            tp1_lot_size=tp1_lot,
            tp2_lot_size=tp2_lot,
            entry_price=executed_price,
            tp1=adjusted_tp1,
            tp2=adjusted_tp2,
            stop_loss=adjusted_sl,
        )

        # ── 6. Create Trade record ─────────────────────────────────────────
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
            entry_lots=filled_volume,
            current_lots=filled_volume,
            stop_loss=plan.stop_loss,
            tp1=plan.tp1,
            tp2=plan.tp2,
            opened_at=ts,
            created_at=ts,
            updated_at=ts,
        )

        self._store.add(trade)
        self._repo.save(trade)
        with self._pending_lock:
            self._release(signal.symbol)

        # ── 7. Emit ────────────────────────────────────────────────────────
        self._bus.emit(Events.TRADE_OPENED, trade)
        metrics.increment("trades.opened")
        metrics.set_gauge("trades.open_count", len(self._store.get_open_trades()))

        # ── [5] Latency metrics ────────────────────────────────────────────
        # signal_to_trade_ms: full pipeline from when signal was triggered
        # (uses signal.triggered_at so candle-to-trade latency is captured,
        #  not just queue-to-trade)
        signal_to_trade_ms = ts - (signal.triggered_at or pipeline_start_ms)
        pipeline_only_ms = ts - pipeline_start_ms  # queue dequeue → trade opened

        metrics.set_gauge("latency.signal_to_trade_ms", signal_to_trade_ms)
        metrics.set_gauge("latency.pipeline_ms", pipeline_only_ms)
        metrics.set_gauge("latency.broker_round_trip_ms", broker_round_trip_ms)

        logger.info(
            "Trade opened",
            extra={
                "trade_id": trade.id,
                "signal_id": signal.id,
                "ticket": ticket,
                "entry_price": executed_price,
                "planned_lots": (
                    plan.lot_size
                    if filled_volume == plan.lot_size
                    else f"{plan.lot_size} (plan)"
                ),
                "filled_lots": filled_volume,
                "tp1_lots": tp1_lot,
                "tp2_lots": tp2_lot,
                # [5] latency breakdown
                "signal_to_trade_ms": signal_to_trade_ms,
                "pipeline_ms": pipeline_only_ms,
                "broker_round_trip_ms": broker_round_trip_ms,
            },
        )
        return trade


# ── Helpers ───────────────────────────────────────────────────────────────────


def _split_lots(
    total: float,
    tp1_pct: float,
    lot_step: float,
) -> tuple[float, float]:
    """Split *total* lots into (tp1, tp2) floored to lot_step."""
    tp1 = math.floor(total * (tp1_pct / 100.0) / lot_step) * lot_step
    tp2 = math.floor((total - tp1) / lot_step) * lot_step
    return round(tp1, 2), round(tp2, 2)
