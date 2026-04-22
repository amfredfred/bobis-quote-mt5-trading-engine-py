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
        self._stub_miss_count: dict[str, int] = {}  # trade_id → consecutive poll misses
        self._stub_miss_limit = 3  # evict stub after this many consecutive misses
        # Last price seen for each leg — used to classify SL vs TP on disappearance
        self._last_tp2_price:   dict[int, float] = {}  # tp2_ticket   → price
        self._last_entry_price: dict[int, float] = {}  # entry_ticket → price

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

        # Load saved records to restore plan data MT5 doesn't store.
        # Index by both entry_ticket (TP1 leg) and tp2_ticket (TP2 leg) so we
        # can match whichever leg(s) are still open after a server outage.
        saved_by_ticket: dict[int, Trade] = {}
        try:
            for t in self._repo.load_open_trades():
                if t.entry_ticket is not None:
                    saved_by_ticket[t.entry_ticket] = t
                if t.tp2_ticket is not None:
                    saved_by_ticket[t.tp2_ticket] = t
        except Exception:
            logger.warning(
                "PositionManager.hydrate_from_broker: could not read saved records — "
                "using broker data only, tp1/tp2 management unavailable for existing positions"
            )

        seen_trade_ids: set[str] = set()
        trades: list[Trade] = []
        for pos in broker_positions:
            if pos.ticket in saved_by_ticket:
                trade = saved_by_ticket[pos.ticket]
                if trade.id in seen_trade_ids:
                    # Already added via the other leg — just sync live fields
                    continue
                seen_trade_ids.add(trade.id)
                # If TP1 leg is gone but TP2 leg is in MT5, mark tp1 as already hit
                if pos.ticket == trade.tp2_ticket and not trade.tp1_hit:
                    trade.tp1_hit = True
                    trade.tp1_hit_at = pos.open_time
                    trade.status = __import__('interfaces.trade', fromlist=['TradeStatus']).TradeStatus.PARTIALLY_CLOSED
                    logger.info(
                        "PositionManager.hydrate_from_broker: TP1 leg absent — "
                        "marking tp1_hit=True for trade %s",
                        trade.id,
                    )
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
            if trade.id not in seen_trade_ids:
                seen_trade_ids.add(trade.id)
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
        # Refresh daily loss cache on every poll cycle
        try:
            loss_pct = self._mt5_pos.get_daily_loss_pct(self._cfg.magic)
            self._execution_engine.update_daily_loss(loss_pct)
        except Exception:
            logger.warning("PositionManager: failed to refresh daily loss pct")

        try:
            broker_positions = self._mt5_pos.get_open_positions(self._cfg.magic)
        except Exception:
            logger.warning("PositionManager: failed to fetch broker positions")
            return

        broker_tickets = {p.ticket for p in broker_positions}
        broker_by_ticket = {p.ticket: p for p in broker_positions}
        store_trades = self._store.get_open_trades()

        # All tickets we already track (both legs)
        tracked_tickets: set[int] = set()
        for t in store_trades:
            if t.entry_ticket:
                tracked_tickets.add(t.entry_ticket)
            if t.tp2_ticket:
                tracked_tickets.add(t.tp2_ticket)

        # ── Reconcile: positions in MT5 but missing from store ────────────
        for pos in broker_positions:
            if pos.ticket not in tracked_tickets:
                saved = self._repo.load_by_ticket(pos.ticket)
                trade = saved if saved else _trade_stub_from_position(pos)
                if not saved:
                    self._repo.save(trade)
                    logger.warning(
                        "PositionManager._poll: ticket=%d %s not in store — stub added",
                        pos.ticket,
                        pos.symbol,
                    )
                if not self._store.get_by_ticket(pos.ticket):
                    self._store.add(trade)

        # ── Refresh store_trades after reconcile ──────────────────────────
        open_trades = self._store.get_open_trades()

        # ── Update last known price for both legs ─────────────────────────
        for trade in open_trades:
            if trade.tp2_ticket and trade.tp2_ticket in broker_by_ticket:
                self._last_tp2_price[trade.tp2_ticket] = broker_by_ticket[trade.tp2_ticket].current_price
            if trade.entry_ticket and trade.entry_ticket in broker_by_ticket:
                self._last_entry_price[trade.entry_ticket] = broker_by_ticket[trade.entry_ticket].current_price

        # ── Lifecycle management ──────────────────────────────────────────
        for trade in open_trades:
            if trade.entry_ticket is None:
                continue

            is_stub = trade.id.startswith("STUB_")
            has_tp2_leg = trade.tp2_ticket is not None

            # ── TP1 leg disappeared → broker closed it at TP1 ─────────────
            if not trade.tp1_hit and trade.entry_ticket not in broker_tickets:
                if is_stub:
                    misses = self._stub_miss_count.get(trade.id, 0) + 1
                    self._stub_miss_count[trade.id] = misses
                    if misses < self._stub_miss_limit:
                        logger.warning(
                            "PositionManager: STUB ticket=%s not in broker — miss %d/%d",
                            trade.entry_ticket, misses, self._stub_miss_limit,
                        )
                        continue
                    self._stub_miss_count.pop(trade.id, None)
                    # Stub with no tp2_ticket — treat as fully closed
                    if not has_tp2_leg:
                        self._handle_position_gone(trade)
                        continue

                if has_tp2_leg:
                    # TP1 leg closed by broker — move TP2 leg SL to BE
                    self._handle_tp1_broker_closed(trade)
                else:
                    # No split — legacy single-leg or stub — gone means closed
                    self._handle_position_gone(trade)
                continue

            # ── TP2 leg disappeared → broker closed it (TP2 or SL) ────────
            if has_tp2_leg and not trade.tp2_hit and trade.tp2_ticket not in broker_tickets:
                self._handle_tp2_broker_closed(trade)
                continue

            # ── Both legs present — reset miss counter ─────────────────────
            self._stub_miss_count.pop(trade.id, None)

    # ── Trade lifecycle handlers ──────────────────────────────────────────────

    def _handle_tp1_broker_closed(self, trade: Trade) -> None:
        """
        TP1 leg (entry_ticket) has disappeared from MT5 — the broker closed it
        at the TP1 price we set.  Our job here is purely state bookkeeping and
        moving the TP2 leg SL to breakeven.  No partial-close order needed.
        """
        logger.info(
            "TP1 leg closed by broker",
            extra={"trade_id": trade.id, "symbol": trade.symbol, "ticket": trade.entry_ticket},
        )

        be_ok = False
        if self._cfg.move_sl_to_be_on_tp1 and trade.tp2_ticket:
            try:
                self._mt5_orders.modify_position(
                    ticket=trade.tp2_ticket,
                    sl=trade.entry_price,
                    tp=trade.tp2,
                )
                be_ok = True
            except Exception:
                logger.exception(
                    "PositionManager: BE move failed for TP2 leg — SL stays at original",
                    extra={"trade_id": trade.id, "tp2_ticket": trade.tp2_ticket},
                )

        new_sl = trade.entry_price if be_ok else trade.stop_loss
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

    def _handle_tp2_broker_closed(self, trade: Trade) -> None:
        """
        TP2 leg (tp2_ticket) has disappeared from MT5 — broker hit either TP2
        or SL.  Use last known price to classify the close reason.
        """
        last_price = self._last_tp2_price.pop(trade.tp2_ticket, None)
        is_buy = trade.side.value == "BUY"

        if last_price is not None:
            tp2_hit = (last_price >= trade.tp2) if is_buy else (last_price <= trade.tp2)
            sl_hit  = (last_price <= trade.stop_loss) if is_buy else (last_price >= trade.stop_loss)
        else:
            # No in-memory price (process restarted while position was open).
            # Query MT5 deal history for this ticket before defaulting to SL_HIT,
            # so a broker TP hit during a restart window isn't counted as a loss.
            deal_price = self._mt5_pos.get_deal_price_for_ticket(trade.tp2_ticket)
            if deal_price is not None:
                tp2_hit = (deal_price >= trade.tp2) if is_buy else (deal_price <= trade.tp2)
                sl_hit  = (deal_price <= trade.stop_loss) if is_buy else (deal_price >= trade.stop_loss)
                last_price = deal_price
                logger.info(
                    "PositionManager._handle_tp2_broker_closed: resolved close price "
                    "from deal history  ticket=%s  price=%s",
                    trade.tp2_ticket, deal_price,
                )
            else:
                # Still nothing — truly unresolvable; log loudly and default to SL.
                logger.warning(
                    "PositionManager._handle_tp2_broker_closed: no price or deal history "
                    "for ticket=%s — defaulting to SL_HIT; check MT5 deal history manually",
                    trade.tp2_ticket,
                )
                tp2_hit = False
                sl_hit  = True

        close_reason = CloseReason.TP2_HIT if tp2_hit else CloseReason.SL_HIT
        close_price  = last_price or trade.tp2

        realized_rr = (
            abs(close_price - trade.entry_price) / abs(trade.entry_price - trade.stop_loss)
            if trade.entry_price and trade.stop_loss != trade.entry_price
            else 0.0
        )
        if close_reason == CloseReason.SL_HIT and realized_rr > 0:
            # This is a manual close, not a TP2 hit, if the price is on the "winning" side of the entry
            # but we didn't see a TP2 hit in MT5 (e.g. due to a missed poll or MT5 outage).
            # We classify it as MANUAL rather than TP2_HIT to avoid inflating the win counter.
            close_reason = CloseReason.MANUAL

        logger.info(
            "TP2 leg closed by broker",
            extra={
                "trade_id":     trade.id,
                "symbol":       trade.symbol,
                "tp2_ticket":   trade.tp2_ticket,
                "close_reason": close_reason.value,
                "close_price":  close_price,
                "realized_rr":  round(realized_rr, 2),
            },
        )

        updated = self._store.update(
            trade.id,
            tp2_hit=tp2_hit,
            tp2_hit_at=now_ms() if tp2_hit else None,
            sl_hit=sl_hit,
            sl_hit_at=now_ms() if sl_hit else None,
            status=TradeStatus.CLOSED,
            close_reason=close_reason,
            close_price=close_price,
            closed_at=now_ms(),
            realized_rr=realized_rr,
        )
        if updated:
            self._store.remove(updated.id)
            self._repo.save(updated)   # persist CLOSED status so reconcile won't re-add this trade
            if tp2_hit:
                self._bus.emit(Events.TRADE_TP2_HIT, updated)
            else:
                self._bus.emit(Events.TRADE_SL_HIT, updated)
            self._bus.emit(Events.TRADE_CLOSED, updated)
            metrics.increment("trades.tp2_hit" if tp2_hit else "trades.sl_hit")
            metrics.set_gauge("trades.open_count", len(self._store.get_open_trades()))

    def _handle_position_gone(self, trade: Trade) -> None:
        if trade.tp2_hit:
            return

        is_stub = trade.id.startswith("STUB_")

        if is_stub:
            close_reason = CloseReason.CLOSED_WHILE_DOWN
            tp1_hit = False
        else:
            # Use last known price to distinguish broker TP1 from SL/manual close.
            # Without this, a broker-side TP hit on a single-leg trade would be
            # misclassified as SL_HIT, inflating the loss counter.
            last_price = self._last_entry_price.pop(trade.entry_ticket, None)
            is_buy = trade.side.value == "BUY"
            if last_price is not None and trade.tp1:
                tp1_hit = (last_price >= trade.tp1) if is_buy else (last_price <= trade.tp1)
            else:
                # No price history (e.g. restarted after close) — fall back to SL.
                # Better than silently mis-counting a win as a loss.
                tp1_hit = False
                logger.warning(
                    "PositionManager._handle_position_gone: no last price for ticket=%s "
                    "— defaulting to SL_HIT; verify manually if TP was hit",
                    trade.entry_ticket,
                )
            close_reason = CloseReason.TP1_HIT if tp1_hit else CloseReason.SL_HIT

        logger.info(
            "Position gone from broker",
            extra={
                "trade_id":    trade.id,
                "symbol":      trade.symbol,
                "ticket":      trade.entry_ticket,
                "close_reason": close_reason.value,
            },
        )
        updated = self._store.update(
            trade.id,
            status=TradeStatus.CLOSED,
            close_reason=close_reason,
            closed_at=now_ms(),
            tp1_hit=tp1_hit if not is_stub else False,
            tp1_hit_at=now_ms() if (not is_stub and tp1_hit) else None,
            sl_hit=(not is_stub and not tp1_hit),
            sl_hit_at=now_ms() if (not is_stub and not tp1_hit) else None,
        )
        if updated:
            self._store.remove(updated.id)
            self._repo.save(updated)   # persist CLOSED status
            if not is_stub:
                if tp1_hit:
                    self._bus.emit(Events.TRADE_TP1_HIT, updated)
                else:
                    self._bus.emit(Events.TRADE_SL_HIT, updated)
            self._bus.emit(Events.TRADE_CLOSED, updated)
            metric_key = "trades.tp1_hit" if tp1_hit else ("trades.sl_hit" if not is_stub else "trades.stub_closed")
            metrics.increment(metric_key)
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
