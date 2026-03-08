"""
Polls MT5 positions on a timer to manage the full trade lifecycle:

  - Reconcile on startup after reboot (trades closed while engine was down)
  - Partial close at TP1 + optional SL to breakeven
  - Full close at TP2
  - Detect SL hits (position absent from MT5)
  - Persist all state changes

Reboot recovery
---------------
On start(), before the first normal poll, reconcile() is called once.
It compares every OPEN/PARTIALLY_CLOSED trade in the store against MT5.
Any trade whose ticket is no longer in MT5 is closed immediately with
close_reason=CLOSED_WHILE_DOWN so the store, disk, and daily stats are
all accurate before the engine resumes normal operation.
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
        event_bus: EventBus,
        exec_config: ExecutionConfig,
        poll_interval: float = 5.0,
    ) -> None:
        self._store = store
        self._mt5_pos = mt5_pos
        self._mt5_orders = mt5_orders
        self._repo = repository
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

    # ── Reboot reconciliation ─────────────────────────────────────────────────

    def reconcile(self) -> None:
        """
        Called once at startup after hydration.

        Compares every open trade in the store against live MT5 positions.
        Trades whose tickets are gone from MT5 were closed while the engine
        was down — mark them CLOSED so the store and disk are accurate before
        normal polling begins.

        This prevents:
          - Stale OPEN trades blocking the max_open_trades risk rule
          - Daily loss / stats being calculated from an incomplete picture
          - The position manager trying to manage a position that no longer exists
        """
        open_trades = self._store.get_open_trades()
        if not open_trades:
            logger.info("PositionManager.reconcile: no open trades to reconcile")
            return

        try:
            broker_positions = self._mt5_pos.get_open_positions(self._cfg.magic)
            print(f"broker_positions: {len(broker_positions)}")
        except Exception:
            logger.warning(
                "PositionManager.reconcile: cannot fetch MT5 positions — "
                "skipping reconciliation, trades will be checked on first poll"
            )
            return

        broker_tickets = {p.ticket for p in broker_positions}
        stale_count = 0

        for trade in open_trades:
            if trade.entry_ticket is None:
                continue

            if trade.entry_ticket not in broker_tickets:
                # Position is gone — closed while engine was down
                self._close_stale(trade)
                stale_count += 1

        logger.info(
            "PositionManager.reconcile complete",
            extra={
                "open_in_store": len(open_trades),
                "live_in_mt5": len(broker_tickets),
                "stale_closed": stale_count,
                "still_open": len(open_trades) - stale_count,
            },
        )

    def _close_stale(self, trade: Trade) -> None:
        """
        Mark a trade as closed because it was no longer in MT5 on startup.

        We don't know the exact close price or reason (SL / TP / manual close),
        so we log it clearly for manual review. The close_reason is set to
        CLOSED_WHILE_DOWN so it can be filtered separately in reports.
        """
        logger.warning(
            "Stale trade detected — closed while engine was down",
            extra={
                "trade_id": trade.id,
                "symbol": trade.symbol,
                "ticket": trade.entry_ticket,
                "status_was": trade.status.value,
                "opened_at": trade.opened_at,
            },
        )

        updated = self._store.update(
            trade.id,
            status=TradeStatus.CLOSED,
            close_reason=CloseReason.CLOSED_WHILE_DOWN,
            closed_at=now_ms(),
        )
        if updated:
            self._repo.save(updated)
            self._bus.emit(Events.TRADE_CLOSED, updated)
            metrics.increment("trades.closed_while_down")
            metrics.set_gauge("trades.open_count", len(self._store.get_open_trades()))

    # ── Poll loop ─────────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while not self._stopped.is_set():
            try:
                self._poll()
            except Exception:
                logger.exception("PositionManager: unhandled error in poll")
            self._stopped.wait(timeout=self._poll_interval)

    def _poll(self) -> None:
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
        logger.info("TP1 hit", extra={"trade_id": trade.id, "price": price})
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
        logger.info("TP2 hit", extra={"trade_id": trade.id, "price": price})
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
            self._repo.save(updated)
            self._bus.emit(Events.TRADE_TP2_HIT, updated)
            self._bus.emit(Events.TRADE_CLOSED, updated)
            metrics.increment("trades.tp2_hit")
            metrics.set_gauge("trades.open_count", len(self._store.get_open_trades()))

    def _handle_position_gone(self, trade: Trade) -> None:
        if trade.tp2_hit:
            return
        logger.info(
            "Position gone from broker",
            extra={"trade_id": trade.id, "ticket": trade.entry_ticket},
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
            self._repo.save(updated)
            self._bus.emit(Events.TRADE_SL_HIT, updated)
            self._bus.emit(Events.TRADE_CLOSED, updated)
            metrics.increment("trades.sl_hit")
            metrics.set_gauge("trades.open_count", len(self._store.get_open_trades()))
