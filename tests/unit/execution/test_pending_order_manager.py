"""PendingOrderManager: poll-loop transitions for resting limit orders.

fill -> real Trade handed to PositionStore; our own expiry -> cancel;
vanished (gone from both orders_get and positions_get) -> dropped.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from src.core.events import Events
from src.domain.position import PositionSide
from src.domain.trade import OrderSide, TradePlan
from src.positions.pending_manager import PendingOrderManager
from src.positions.pending_store import PendingOrderRecord, PendingOrderStore
from src.positions.store import PositionStore
from src.utils.time import now_ms


def _plan(symbol: str = "XAUUSD", side: OrderSide = OrderSide.BUY, signal=None) -> TradePlan:
    return TradePlan(
        signal_id="sig-1",
        symbol=symbol,
        side=side,
        entry_price=100.0,
        stop_loss=95.0,
        tp1=105.0,
        tp2=110.0,
        lot_size=0.01,
        risk_amount=10.0,
        risk_percent=1.0,
        risk_reward_ratio=3.0,
        planned_at=1,
        signal=signal,
    )


def _record(ticket: int = 1, expiry_at: int | None = None, signal=None) -> PendingOrderRecord:
    ts = now_ms()
    return PendingOrderRecord(
        ticket=ticket,
        plan=_plan(signal=signal),
        placed_at=ts,
        expiry_at=expiry_at if expiry_at is not None else ts + 3_600_000,  # 1h out, not due
    )


def _manager(cluster_tracker=None):
    pending_store = PendingOrderStore()
    position_store = PositionStore()
    mt5_pos = MagicMock()
    mt5_orders = MagicMock()
    repo = MagicMock()
    repo.save.return_value = True
    bus = MagicMock()
    exec_config = MagicMock()
    exec_config.magic = 8858
    exec_config.limit_order_expiry_seconds = 1800
    manager = PendingOrderManager(
        pending_store=pending_store,
        position_store=position_store,
        mt5_pos=mt5_pos,
        mt5_orders=mt5_orders,
        repository=repo,
        event_bus=bus,
        exec_config=exec_config,
        cluster_tracker=cluster_tracker,
    )
    return manager, pending_store, position_store, mt5_pos, mt5_orders, bus


def test_track_adds_to_pending_store():
    manager, pending_store, *_ = _manager()
    record = _record()
    manager.track(record)
    assert pending_store.get(record.ticket) is not None


def test_fill_detected_creates_trade_and_removes_from_pending():
    manager, pending_store, position_store, mt5_pos, mt5_orders, bus = _manager()
    record = _record(ticket=42)
    manager.track(record)

    filled_position = MagicMock(ticket=42, open_price=101.5, lots=0.01)
    mt5_pos.get_pending_orders.return_value = []  # no longer resting
    mt5_pos.get_open_positions.return_value = [filled_position]  # now a real position

    manager._poll()

    assert pending_store.get(42) is None
    open_trades = position_store.get_open_trades()
    assert len(open_trades) == 1
    assert open_trades[0].entry_ticket == 42
    assert open_trades[0].entry_price == 101.5
    assert open_trades[0].entry_lots == 0.01
    assert any(call.args[0] == Events.TRADE_OPENED for call in bus.emit.call_args_list)


def test_still_resting_and_not_expired_stays_tracked():
    manager, pending_store, position_store, mt5_pos, mt5_orders, bus = _manager()
    record = _record(ticket=7)  # expiry_at 1h in the future
    manager.track(record)

    resting_order = MagicMock(ticket=7)
    mt5_pos.get_pending_orders.return_value = [resting_order]
    mt5_pos.get_open_positions.return_value = []

    manager._poll()

    assert pending_store.get(7) is not None
    mt5_orders.cancel_pending_order.assert_not_called()
    assert position_store.get_open_trades() == []


def test_our_own_expiry_cancels_and_removes():
    manager, pending_store, position_store, mt5_pos, mt5_orders, bus = _manager()
    past = now_ms() - 1_000  # already expired
    record = _record(ticket=9, expiry_at=past)
    manager.track(record)

    resting_order = MagicMock(ticket=9)
    mt5_pos.get_pending_orders.return_value = [resting_order]
    mt5_pos.get_open_positions.return_value = []

    manager._poll()

    mt5_orders.cancel_pending_order.assert_called_once_with(9)
    assert pending_store.get(9) is None


def test_vanished_order_is_dropped_without_cancel():
    manager, pending_store, position_store, mt5_pos, mt5_orders, bus = _manager()
    record = _record(ticket=13)
    manager.track(record)

    # Gone from both orders_get() and positions_get() — broker already
    # resolved it (expired/rejected/cancelled) for a reason other than our
    # own expiry check.
    mt5_pos.get_pending_orders.return_value = []
    mt5_pos.get_open_positions.return_value = []

    manager._poll()

    mt5_orders.cancel_pending_order.assert_not_called()
    assert pending_store.get(13) is None
    assert position_store.get_open_trades() == []


def test_poll_with_no_tracked_records_does_nothing():
    manager, pending_store, position_store, mt5_pos, mt5_orders, bus = _manager()

    manager._poll()

    mt5_pos.get_pending_orders.assert_not_called()
    mt5_pos.get_open_positions.assert_not_called()


# ── Cluster-risk reservation symmetry ────────────────────────────────────────
# ExecutionEngine.execute() reserves a signal before routing to the limit
# path; this module is what finally resolves that reservation one way or
# the other once the resting order's fate is known.


def test_fill_marks_trade_opened_on_cluster_tracker():
    cluster_tracker = MagicMock()
    manager, pending_store, position_store, mt5_pos, mt5_orders, bus = _manager(cluster_tracker)
    signal = SimpleNamespace(id="sig-1")
    record = _record(ticket=42, signal=signal)
    manager.track(record)

    filled_position = MagicMock(ticket=42, open_price=101.5, lots=0.01)
    mt5_pos.get_pending_orders.return_value = []
    mt5_pos.get_open_positions.return_value = [filled_position]

    manager._poll()

    cluster_tracker.mark_trade_opened.assert_called_once()
    cluster_tracker.release_signal.assert_not_called()


def test_our_own_expiry_releases_cluster_reservation():
    cluster_tracker = MagicMock()
    manager, pending_store, position_store, mt5_pos, mt5_orders, bus = _manager(cluster_tracker)
    signal = SimpleNamespace(id="sig-1")
    past = now_ms() - 1_000
    record = _record(ticket=9, expiry_at=past, signal=signal)
    manager.track(record)

    mt5_pos.get_pending_orders.return_value = [MagicMock(ticket=9)]
    mt5_pos.get_open_positions.return_value = []

    manager._poll()

    cluster_tracker.release_signal.assert_called_once_with(signal)
    cluster_tracker.mark_trade_opened.assert_not_called()


def test_vanished_order_releases_cluster_reservation():
    cluster_tracker = MagicMock()
    manager, pending_store, position_store, mt5_pos, mt5_orders, bus = _manager(cluster_tracker)
    signal = SimpleNamespace(id="sig-1")
    record = _record(ticket=13, signal=signal)
    manager.track(record)

    mt5_pos.get_pending_orders.return_value = []
    mt5_pos.get_open_positions.return_value = []

    manager._poll()

    cluster_tracker.release_signal.assert_called_once_with(signal)
    cluster_tracker.mark_trade_opened.assert_not_called()


def test_no_cluster_tracker_is_safe_noop():
    # cluster_tracker=None (the default) — every transition must still work.
    manager, pending_store, position_store, mt5_pos, mt5_orders, bus = _manager(cluster_tracker=None)
    record = _record(ticket=1)
    manager.track(record)

    mt5_pos.get_pending_orders.return_value = []
    mt5_pos.get_open_positions.return_value = []

    manager._poll()  # must not raise

    assert pending_store.get(1) is None


# ── Startup hydration ─────────────────────────────────────────────────────────


def _broker_order(ticket=99, symbol="XAUUSD", side=PositionSide.BUY, price=2000.0,
                   sl=1995.0, tp=2020.0, lots=0.01, setup_time=None, expiration=0):
    return SimpleNamespace(
        ticket=ticket, symbol=symbol, side=side, price=price, stop_loss=sl,
        take_profit=tp, lots=lots, setup_time=setup_time or now_ms(), expiration=expiration,
    )


def test_hydrate_from_broker_restores_resting_orders_as_stubs():
    manager, pending_store, *_rest, mt5_pos, mt5_orders, bus = _manager()
    mt5_pos.get_pending_orders.return_value = [_broker_order(ticket=99)]

    manager.hydrate_from_broker()

    record = pending_store.get(99)
    assert record is not None
    assert record.plan.symbol == "XAUUSD"
    assert record.plan.side == OrderSide.BUY
    assert record.plan.entry_price == 2000.0
    assert record.plan.signal is None  # stub — no original signal recoverable
    assert record.plan.tp1_lots == 0.0  # partial close unavailable for stubs


def test_hydrate_from_broker_falls_back_to_config_expiry_when_mt5_reports_none():
    manager, pending_store, *_rest, mt5_pos, mt5_orders, bus = _manager()
    mt5_pos.get_pending_orders.return_value = [_broker_order(ticket=1, expiration=0)]

    before = now_ms()
    manager.hydrate_from_broker()

    record = pending_store.get(1)
    # expiration=0 (MT5's "not set") -> falls back to
    # now + exec_config.limit_order_expiry_seconds (1800s in _manager()).
    assert before + 1_800_000 <= record.expiry_at <= now_ms() + 1_800_000


def test_hydrate_from_broker_with_no_resting_orders_leaves_store_empty():
    manager, pending_store, *_rest, mt5_pos, mt5_orders, bus = _manager()
    mt5_pos.get_pending_orders.return_value = []

    manager.hydrate_from_broker()

    assert pending_store.size() == 0


def test_hydrate_from_broker_survives_mt5_failure():
    manager, pending_store, *_rest, mt5_pos, mt5_orders, bus = _manager()
    mt5_pos.get_pending_orders.side_effect = RuntimeError("MT5 unavailable")

    manager.hydrate_from_broker()  # must not raise

    assert pending_store.size() == 0
