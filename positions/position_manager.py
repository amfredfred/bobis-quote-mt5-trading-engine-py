"""
Polls MT5 positions on a timer to manage the full trade lifecycle:

  - Startup hydration from MT5 + saved trade records
  - Partial close at TP1 + optional SL to breakeven
  - Full close at TP2
  - Detect SL/manual closes (position absent from MT5)
  - Persist plan updates, delete on close
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from brokers.mt5.mt5_orders import Mt5Orders
from brokers.mt5.mt5_positions import Mt5Positions
from brokers.mt5.mt5_types import Mt5OrderType
from config.config import ExecutionConfig
from core.event_bus import EventBus
from core.events import Events
from infrastructure.metrics import metrics
from positions.position_store import PositionStore
from storage.trade_repository import TradeRepository
from interfaces.position import PositionSide
from interfaces.trade import CloseReason, Trade, TradeStatus
from utils.time_utils import now_ms

logger = logging.getLogger(__name__)


class PositionManager:
    def __init__(
        self,
        store: PositionStore,
        mt5_pos: Mt5Positions,
        mt5_orders: Mt5Orders,
        repository: TradeRepository,
        execution_engine,  # ExecutionEngine — avoid circular import
        event_bus: EventBus,
        exec_config: ExecutionConfig,
        poll_interval: float = 5.0,
    ) -> None:
        self._store = store
        self._mt5_pos = mt5_pos
        self._mt5_orders = mt5_orders
        self._repo = repository
        self._execution_engine = execution_engine
        self._bus = event_bus
        self._cfg = exec_config
        self._poll_interval = poll_interval
        self._stopped = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._stopped.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="position-manager",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "PositionManager started", extra={"poll_interval": self._poll_interval}
        )

    def stop(self) -> None:
        self._stopped.set()
        if self._thread:
            self._thread.join(timeout=10)

    # ── Startup hydration ─────────────────────────────────────────────────────

    def hydrate_from_broker(self) -> None:
        """
        Called once at startup. Fetches open positions from MT5 and populates
        the in-memory store.

        MT5 is the source of truth for what is open. Saved trade records in
        data/trades/ are merged in to restore plan data (signal_id, tp1/tp2
        levels, lot split) that MT5 doesn't store. Positions with no saved
        record get a minimal stub — they are tracked but TP1 partial close
        is unavailable for them.

        The first _poll() cycle handles anything closed while the engine was
        down — no separate reconciliation step needed.
        """
        try:
            broker_positions = self._mt5_pos.get_open_positions(self._cfg.magic)
        except Exception:
            logger.warning(
                "PositionManager.hydrate_from_broker: cannot fetch MT5 positions — "
                "store will be empty, first poll will populate it"
            )
            return

        if not broker_positions:
            logger.info("PositionManager.hydrate_from_broker: no open positions in MT5")
            return

        # Load saved records to restore plan data MT5 doesn't store
        saved_by_ticket: dict[int, Trade] = {}
        try:
            for t in self._repo.load_open_trades():
                if t.entry_ticket is not None:
                    saved_by_ticket[t.entry_ticket] = t
        except Exception:
            logger.warning(
                "PositionManager.hydrate_from_broker: could not read saved records — "
                "using broker data only, tp1/tp2 management unavailable for existing positions"
            )

        trades: list[Trade] = []
        for pos in broker_positions:
            if pos.ticket in saved_by_ticket:
                trade = saved_by_ticket[pos.ticket]
                # Sync live broker fields in case they changed while engine was down
                trade.current_lots = pos.lots
                trade.stop_loss = pos.stop_loss
            else:
                trade = _trade_stub_from_position(pos)
                self._repo.save(trade)
                logger.warning(
                    "PositionManager.hydrate_from_broker: no saved record for ticket=%d "
                    "symbol=%s — stub created and saved",
                    pos.ticket,
                    pos.symbol,
                )
            trades.append(trade)

        self._store.hydrate(trades)
        logger.info(
            "PositionManager.hydrate_from_broker complete",
            extra={
                "positions_from_mt5": len(broker_positions),
                "matched_to_records": len(saved_by_ticket),
                "stubs_created": len(broker_positions) - len(saved_by_ticket),
                "store_size": len(self._store.get_open_trades()),
            },
        )

    # ── Poll loop ─────────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while not self._stopped.is_set():
            try:
                self._poll()
            except Exception:
                logger.exception("PositionManager: unhandled error in poll")
            self._stopped.wait(timeout=self._poll_interval)

    def _poll(self) -> None:
        # Refresh daily loss cache on every poll cycle (~5s granularity is sufficient)
        try:
            loss_pct = self._mt5_pos.get_daily_loss_pct(self._cfg.magic)
            self._execution_engine.update_daily_loss(loss_pct)
        except Exception:
            logger.warning("PositionManager: failed to refresh daily loss pct")

        open_trades = self._store.get_open_trades()
        if not open_trades:
            return

        try:
            broker_positions = self._mt5_pos.get_open_positions(self._cfg.magic)
        except Exception:
            logger.warning("PositionManager: failed to fetch broker positions")
            return

        broker_tickets = {p.ticket for p in broker_positions}
        broker_by_ticket = {p.ticket: p for p in broker_positions}

        for trade in open_trades:
            if trade.entry_ticket is None:
                continue

            if trade.entry_ticket not in broker_tickets:
                self._handle_position_gone(trade)
                continue

            broker_pos = broker_by_ticket[trade.entry_ticket]
            current = broker_pos.current_price
            is_buy = trade.side.value == "BUY"

            if not trade.tp1_hit:
                if current >= trade.tp1 if is_buy else current <= trade.tp1:
                    self._handle_tp1(trade, current, broker_pos)
                    continue

            if trade.tp1_hit and not trade.tp2_hit:
                if current >= trade.tp2 if is_buy else current <= trade.tp2:
                    self._handle_tp2(trade, current)

    # ── Trade lifecycle handlers ──────────────────────────────────────────────

    def _handle_tp1(self, trade: Trade, price: float, broker_pos) -> None:
        logger.info(
            "TP1 hit",
            extra={"trade_id": trade.id, "symbol": trade.symbol, "price": price},
        )
        try:
            side_type = (
                Mt5OrderType.BUY if trade.side.value == "BUY" else Mt5OrderType.SELL
            )
            tick = self._mt5_pos.get_current_tick(trade.symbol)
            close_price = tick.bid if trade.side.value == "BUY" else tick.ask
            self._mt5_orders.close_position(
                ticket=trade.entry_ticket,
                symbol=trade.symbol,
                side=side_type,
                volume=trade.plan.tp1_lot_size,
                price=close_price,
                slippage=self._cfg.slippage,
                magic=self._cfg.magic,
                comment=f"TP1 {self._cfg.comment}",
            )
            if self._cfg.move_sl_to_be_on_tp1:
                self._mt5_orders.modify_position(
                    ticket=trade.entry_ticket,
                    sl=trade.entry_price,
                    tp=trade.tp2,
                )
        except Exception:
            logger.exception(
                "PositionManager: TP1 action failed", extra={"trade_id": trade.id}
            )

        new_sl = (
            trade.entry_price if self._cfg.move_sl_to_be_on_tp1 else trade.stop_loss
        )
        updated = self._store.update(
            trade.id,
            tp1_hit=True,
            tp1_hit_at=now_ms(),
            current_lots=trade.plan.tp2_lot_size,
            status=TradeStatus.PARTIALLY_CLOSED,
            stop_loss=new_sl,
        )
        if updated:
            self._repo.save(updated)
            self._bus.emit(Events.TRADE_TP1_HIT, updated)
            metrics.increment("trades.tp1_hit")

    def _handle_tp2(self, trade: Trade, price: float) -> None:
        logger.info(
            "TP2 hit",
            extra={"trade_id": trade.id, "symbol": trade.symbol, "price": price},
        )
        realized_rr = (
            abs(price - trade.entry_price) / abs(trade.entry_price - trade.stop_loss)
            if trade.entry_price and trade.stop_loss != trade.entry_price
            else 0.0
        )
        updated = self._store.update(
            trade.id,
            tp2_hit=True,
            tp2_hit_at=now_ms(),
            status=TradeStatus.CLOSED,
            close_reason=CloseReason.TP2_HIT,
            close_price=price,
            closed_at=now_ms(),
            realized_rr=realized_rr,
        )
        if updated:
            self._store.remove(updated.id)
            self._repo.delete(updated.id)
            self._bus.emit(Events.TRADE_TP2_HIT, updated)
            self._bus.emit(Events.TRADE_CLOSED, updated)
            metrics.increment("trades.tp2_hit")
            metrics.set_gauge("trades.open_count", len(self._store.get_open_trades()))

    def _handle_position_gone(self, trade: Trade) -> None:
        if trade.tp2_hit:
            return
        logger.info(
            "Position gone from broker",
            extra={
                "trade_id": trade.id,
                "symbol": trade.symbol,
                "ticket": trade.entry_ticket,
            },
        )
        updated = self._store.update(
            trade.id,
            status=TradeStatus.CLOSED,
            close_reason=CloseReason.SL_HIT,
            closed_at=now_ms(),
            sl_hit=True,
            sl_hit_at=now_ms(),
        )
        if updated:
            self._store.remove(updated.id)
            self._repo.delete(updated.id)
            self._bus.emit(Events.TRADE_SL_HIT, updated)
            self._bus.emit(Events.TRADE_CLOSED, updated)
            metrics.increment("trades.sl_hit")
            metrics.set_gauge("trades.open_count", len(self._store.get_open_trades()))


# ── Module-level helpers ──────────────────────────────────────────────────────


def _trade_stub_from_position(pos) -> Trade:
    """
    Build a minimal Trade from an MT5 Position.
    Used when a broker position has no matching saved record —
    e.g. manually opened trades or records lost to a storage failure.

    tp1_lot_size=0.0 disables partial close for stubs.
    The position is still tracked and SL hits are detected normally.
    """
    from interfaces.trade import OrderSide, TradePlan

    ts = now_ms()
    side = OrderSide.BUY if pos.side == PositionSide.BUY else OrderSide.SELL

    plan = TradePlan(
        signal_id="unknown",
        symbol=pos.symbol,
        side=side,
        entry_price=pos.open_price,
        stop_loss=pos.stop_loss,
        tp1=pos.take_profit,
        tp2=pos.take_profit,
        lot_size=pos.lots,
        tp1_lot_size=0.0,
        tp2_lot_size=pos.lots,
        risk_amount=0.0,
        risk_percent=0.0,
        risk_reward_ratio=0.0,
        planned_at=ts,
        signal=None,
    )

    return Trade(
        id=f"STUB_{pos.symbol}_{pos.ticket}_{side.value}",
        signal_id="unknown",
        symbol=pos.symbol,
        side=side,
        status=TradeStatus.OPEN,
        plan=plan,
        entry_ticket=pos.ticket,
        entry_price=pos.open_price,
        entry_lots=pos.lots,
        current_lots=pos.lots,
        stop_loss=pos.stop_loss,
        tp1=pos.take_profit,
        tp2=pos.take_profit,
        opened_at=pos.open_time,
        created_at=ts,
        updated_at=ts,
    )
