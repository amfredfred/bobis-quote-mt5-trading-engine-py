"""
Polls MT5 pending (resting limit) orders on a timer, same pattern
PositionManager uses for open positions:

  - Fill detected (ticket graduates from orders_get() into positions_get())
    -> construct the real Trade from the ORIGINAL TradePlan (not a stub —
       we placed this order ourselves and still have the full plan/signal),
       hand off to PositionStore for normal TP1/BE/TP2 lifecycle tracking.
  - Still resting past our own expiry_at -> actively cancel. MT5 also
    auto-cancels server-side via the order's own `expiration` field (see
    Mt5Orders.open_limit_order) — this is a belt-and-suspenders check, not
    the primary mechanism.
  - Ticket gone from both orders_get() and positions_get() -> rejected or
    cancelled by MT5/broker for a reason other than our own expiry check;
    dropped, logged.

Deliberately NOT reusing ExecutionEngine.execute()'s trade-finalization
latency metrics (signal_to_trade_ms and friends) — those measure
signal-detection-to-execution speed, which is meaningless for a fill that
happened whenever price eventually reached a resting order, possibly
`expiry_seconds` later. This module's own, simpler finalization only
tracks what's actually meaningful here: that the order filled, and when.

Cluster-risk symmetry: ExecutionEngine.execute() calls
cluster_tracker.reserve_signal() before routing to either the market or
limit path. A market fill converts that reservation into
mark_trade_opened(); a market-path failure releases it. A LIMIT order
sits in between those two outcomes until it resolves — the reservation
stays live while it's resting, and THIS module is what finally resolves
it: mark_trade_opened() on fill, release_signal() on expiry/vanish (no
trade ever materialized, so nothing should stay reserved against it).

Startup hydration: mirrors PositionManager.hydrate_from_broker() exactly —
same stub tradeoff. MT5 only reports a resting order's own broker-visible
fields (symbol, side, price, sl, tp, setup/expiration time); it has no
concept of OUR signal_id, risk_amount, or tp1/tp2 split. A pending order
still resting after a restart gets a STUB TradePlan (signal=None,
tp1_lots=0.0) — enough to detect its eventual fill and open a real Trade
for it, but with the same "no partial-close data" and "no cluster_tracker
release on expiry/vanish" limitation stub Trades already accept elsewhere
(see _release_cluster_reservation's None-signal guard). Not fabricating
risk data we don't have is the same discipline this whole project applies
to backtesting; guessing here would be worse than the honest gap.
"""

from __future__ import annotations

import logging
import threading
import uuid
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from src.risk.cluster_tracker import ClusterRiskTracker

from src.brokers.mt5.orders import Mt5Orders
from src.brokers.mt5.positions import Mt5Positions
from src.config.settings import ExecutionConfig
from src.core.event_bus import EventBus
from src.core.events import Events
from src.domain.position import PendingOrder, PositionSide
from src.domain.trade import OrderSide, Trade, TradePlan, TradeStatus
from src.infra.database import TradeRepository
from src.infra.metrics import metrics
from src.positions.sl_cascade import cascade_sl_to_stacked_position
from src.positions.store import PositionStore
from src.utils.time import now_ms
from .pending_store import PendingOrderRecord, PendingOrderStore

logger = logging.getLogger(__name__)


class PendingOrderManager:
    def __init__(
        self,
        pending_store: PendingOrderStore,
        position_store: PositionStore,
        mt5_pos: Mt5Positions,
        mt5_orders: Mt5Orders,
        repository: TradeRepository,
        event_bus: EventBus,
        exec_config: ExecutionConfig,
        cluster_tracker: "ClusterRiskTracker | None" = None,
        poll_interval: float = 5.0,
    ) -> None:
        self._pending_store = pending_store
        self._position_store = position_store
        self._cluster_tracker = cluster_tracker
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
            target=self._loop, name="pending-order-manager", daemon=True,
        )
        self._thread.start()
        logger.info("PendingOrderManager started", extra={"poll_interval": self._poll_interval})

    def stop(self) -> None:
        self._stopped.set()
        if self._thread:
            self._thread.join(timeout=10)

    # ── Startup hydration ─────────────────────────────────────────────────────

    def hydrate_from_broker(self) -> None:
        """Called once at startup, alongside PositionManager's own
        hydrate_from_broker() — restores tracking for any limit orders
        still resting from before a restart, as stubs (see module
        docstring for exactly what that does and doesn't recover)."""
        try:
            broker_pending = self._mt5_pos.get_pending_orders(self._cfg.magic)
        except Exception:
            logger.warning(
                "PendingOrderManager.hydrate_from_broker: cannot fetch MT5 pending "
                "orders — store will be empty, first poll will pick up anything "
                "MT5 resolves (fill/expiry) in the meantime once rediscovered"
            )
            return

        if not broker_pending:
            logger.info("PendingOrderManager.hydrate_from_broker: no resting orders in MT5")
            return

        for order in broker_pending:
            record = _pending_record_stub_from_order(
                order, fallback_expiry_seconds=self._cfg.limit_order_expiry_seconds,
            )
            self._pending_store.add(record)

        logger.info(
            "PendingOrderManager.hydrate_from_broker complete",
            extra={
                "orders_from_mt5": len(broker_pending),
                "store_size": self._pending_store.size(),
            },
        )
        metrics.set_gauge("pending_orders.open_count", self._pending_store.size())

    # ── Placement ─────────────────────────────────────────────────────────────

    def track(self, record: PendingOrderRecord) -> None:
        """Called right after OrderManager.execute_limit_order succeeds."""
        self._pending_store.add(record)
        metrics.set_gauge("pending_orders.open_count", self._pending_store.size())

    # ── Poll loop ─────────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while not self._stopped.is_set():
            try:
                self._poll()
            except Exception:
                logger.exception("PendingOrderManager: unhandled error in poll")
            self._stopped.wait(timeout=self._poll_interval)

    def _poll(self) -> None:
        records = self._pending_store.get_all()
        if not records:
            return

        try:
            broker_pending = self._mt5_pos.get_pending_orders(self._cfg.magic)
        except Exception:
            logger.warning("PendingOrderManager: failed to fetch broker pending orders")
            return
        try:
            broker_positions = self._mt5_pos.get_open_positions(self._cfg.magic)
        except Exception:
            logger.warning("PendingOrderManager: failed to fetch broker positions")
            return

        still_pending_tickets = {o.ticket for o in broker_pending}
        broker_position_by_ticket = {p.ticket: p for p in broker_positions}
        now = now_ms()

        for record in records:
            if record.ticket in broker_position_by_ticket:
                self._handle_fill(record, broker_position_by_ticket[record.ticket])
                continue

            if record.ticket in still_pending_tickets:
                if now >= record.expiry_at:
                    self._handle_our_expiry(record)
                continue

            # Gone from both — MT5/broker cancelled or rejected it for a
            # reason other than our own expiry check (e.g. its own
            # server-side expiration already fired, or a manual cancel).
            self._handle_vanished(record)

    # ── Transitions ───────────────────────────────────────────────────────────

    def _handle_fill(self, record: PendingOrderRecord, position) -> None:
        plan = record.plan
        ts = now_ms()
        trade = Trade(
            id=str(uuid.uuid4()),
            signal_id=plan.signal_id,
            symbol=plan.symbol,
            side=plan.side,
            status=TradeStatus.OPEN,
            plan=plan,
            entry_ticket=record.ticket,
            entry_price=position.open_price,
            entry_lots=position.lots,
            current_lots=position.lots,
            tp1_lots=plan.tp1_lots,
            stop_loss=plan.stop_loss,
            tp1=plan.tp1,
            tp2=plan.tp2,
            opened_at=ts,
            created_at=ts,
            updated_at=ts,
        )

        try:
            self._position_store.add(trade)
        except Exception:
            logger.exception(
                "Limit order filled but in-memory tracking failed; manual intervention required",
                extra={"trade_id": trade.id, "ticket": record.ticket, "symbol": trade.symbol},
            )
            metrics.increment("trades.tracking_failures")
            self._bus.emit(
                Events.TRADE_ERROR,
                {"signal": plan.signal, "reason": "trade_tracking_failed_after_limit_fill"},
            )
            self._pending_store.remove(record.ticket)
            return

        # Stacking SL cascade — same rule as the market-fill path in
        # ExecutionEngine.execute(); see src/positions/sl_cascade.py.
        cascade_sl_to_stacked_position(self._position_store, self._mt5_orders, trade)

        # Converts the reservation ExecutionEngine.execute() made before
        # routing to this limit path into real open-trade tracking — same
        # call the market-fill path makes on its own success.
        if self._cluster_tracker is not None:
            self._cluster_tracker.mark_trade_opened(trade)

        persisted = self._repo.save(trade)
        if not persisted:
            logger.error(
                "Limit order filled but persistence failed",
                extra={"trade_id": trade.id, "ticket": record.ticket, "symbol": trade.symbol},
            )
            metrics.increment("trades.persistence_failures")

        self._pending_store.remove(record.ticket)
        self._bus.emit(Events.TRADE_OPENED, trade)
        metrics.increment("trades.opened")
        metrics.increment("trades.opened_from_limit_fill")
        metrics.set_gauge("trades.open_count", len(self._position_store.get_open_trades()))
        metrics.set_gauge("pending_orders.open_count", self._pending_store.size())

        logger.info(
            "Limit order filled — trade opened",
            extra={
                "trade_id": trade.id,
                "signal_id": plan.signal_id,
                "ticket": record.ticket,
                "entry_price": position.open_price,
                "filled_lots": position.lots,
                "resting_since": record.placed_at,
                "fill_latency_ms": ts - record.placed_at,
            },
        )

    def _handle_our_expiry(self, record: PendingOrderRecord) -> None:
        try:
            self._mt5_orders.cancel_pending_order(record.ticket)
        except Exception:
            logger.exception(
                "PendingOrderManager: failed to cancel expired limit order",
                extra={"ticket": record.ticket, "symbol": record.plan.symbol},
            )
            return
        self._pending_store.remove(record.ticket)
        self._release_cluster_reservation(record)
        metrics.increment("orders.limit_expired")
        metrics.set_gauge("pending_orders.open_count", self._pending_store.size())
        logger.info(
            "Limit order expired unfilled — cancelled",
            extra={"ticket": record.ticket, "symbol": record.plan.symbol},
        )

    def _handle_vanished(self, record: PendingOrderRecord) -> None:
        self._pending_store.remove(record.ticket)
        self._release_cluster_reservation(record)
        metrics.increment("orders.limit_vanished")
        metrics.set_gauge("pending_orders.open_count", self._pending_store.size())
        logger.info(
            "Pending limit order no longer resting or open — treating as expired/cancelled/rejected",
            extra={"ticket": record.ticket, "symbol": record.plan.symbol},
        )

    def _release_cluster_reservation(self, record: PendingOrderRecord) -> None:
        """No trade ever materialized for this pending order — release the
        cluster_tracker reservation ExecutionEngine.execute() made before
        routing to the limit path, same as a market-path failure would."""
        if self._cluster_tracker is not None and record.plan.signal is not None:
            self._cluster_tracker.release_signal(record.plan.signal)


# ── Module-level helpers ──────────────────────────────────────────────────────


def _pending_record_stub_from_order(
    order: PendingOrder, fallback_expiry_seconds: int,
) -> PendingOrderRecord:
    """Best-effort reconstruction of a resting order found in MT5 at
    startup that this process has no memory of — see module docstring's
    "Startup hydration" section for exactly what's recoverable and what
    isn't. tp1/tp2 both fall back to the broker's single `take_profit`
    (MT5 doesn't store our tp1/tp2 split); tp1_lots=0.0 disables poll-based
    partial close for it, the same tradeoff PositionManager's own
    _trade_stub_from_position accepts for orphaned open positions.
    """
    side = OrderSide.BUY if order.side == PositionSide.BUY else OrderSide.SELL
    plan = TradePlan(
        signal_id="unknown",
        symbol=order.symbol,
        side=side,
        entry_price=order.price,
        stop_loss=order.stop_loss,
        tp1=order.take_profit,
        tp2=order.take_profit,
        lot_size=order.lots,
        risk_amount=0.0,
        risk_percent=0.0,
        risk_reward_ratio=0.0,
        planned_at=order.setup_time,
        signal=None,
        tp1_lots=0.0,
    )
    expiry_at = (
        order.expiration if order.expiration
        else now_ms() + fallback_expiry_seconds * 1000
    )
    return PendingOrderRecord(
        ticket=order.ticket,
        plan=plan,
        placed_at=order.setup_time,
        expiry_at=expiry_at,
    )
