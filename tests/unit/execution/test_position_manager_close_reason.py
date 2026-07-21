"""_handle_position_gone: close-reason classification when price can't be resolved."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.core.events import Events
from src.domain.trade import CloseReason, OrderSide, Trade, TradePlan, TradeStatus
from src.positions.manager import PositionManager


def _trade(*, entry_ticket: int = 555) -> Trade:
    plan = TradePlan(
        signal_id="sig",
        symbol="XAUUSD",
        side=OrderSide.BUY,
        entry_price=100.0,
        stop_loss=95.0,
        tp1=105.0,
        tp2=110.0,
        lot_size=0.01,
        risk_amount=10.0,
        risk_percent=1.0,
        risk_reward_ratio=3.0,
        planned_at=1,
        signal=None,
    )
    return Trade(
        id="trade-1",
        signal_id="sig",
        symbol="XAUUSD",
        side=OrderSide.BUY,
        status=TradeStatus.OPEN,
        plan=plan,
        entry_ticket=entry_ticket,
        entry_price=100.0,
        stop_loss=95.0,
        tp2=110.0,
    )


def _manager() -> tuple[PositionManager, MagicMock, MagicMock, MagicMock]:
    store = MagicMock()
    mt5_pos = MagicMock()
    repo = MagicMock()
    bus = MagicMock()
    manager = PositionManager(
        store=store,
        mt5_pos=mt5_pos,
        mt5_orders=MagicMock(),
        repository=repo,
        execution_engine=MagicMock(),
        event_bus=bus,
        exec_config=MagicMock(),
    )
    return manager, store, mt5_pos, bus


def test_handle_position_gone_reports_unknown_when_price_unresolvable():
    manager, store, mt5_pos, bus = _manager()
    trade = _trade()

    # No cached last_price for this ticket, and MT5 deal history also fails.
    mt5_pos.get_deal_price_for_ticket.return_value = None
    store.update.return_value = trade

    with patch("src.positions.manager.metrics") as mock_metrics:
        manager._handle_position_gone(trade)

    update_kwargs = store.update.call_args.kwargs
    assert update_kwargs["close_reason"] == CloseReason.UNKNOWN
    assert update_kwargs["sl_hit"] is False
    assert update_kwargs["tp2_hit"] is False

    emitted_events = [call.args[0] for call in bus.emit.call_args_list]
    assert Events.TRADE_CLOSED in emitted_events
    assert Events.TRADE_SL_HIT not in emitted_events
    assert Events.TRADE_TP2_HIT not in emitted_events

    metric_calls = [call.args[0] for call in mock_metrics.increment.call_args_list]
    assert "trades.unknown_close" in metric_calls
    assert "trades.sl_hit" not in metric_calls


def test_handle_position_gone_does_not_fabricate_a_win_when_price_unresolvable():
    """Regression: close_price used to fall back to trade.tp2 even when no
    real price was available, fabricating a full TP2 win (and feeding a
    phantom +R into cluster_tracker/equity_throttle) for a genuinely unknown
    outcome. Both close_price and realized_rr must stay None instead, and
    the win/loss/breakeven counters must not fire at all for this trade."""
    manager, store, mt5_pos, bus = _manager()
    trade = _trade()

    mt5_pos.get_deal_price_for_ticket.return_value = None
    store.update.return_value = trade

    with patch("src.positions.manager.metrics") as mock_metrics:
        manager._handle_position_gone(trade)

    update_kwargs = store.update.call_args.kwargs
    assert update_kwargs["close_price"] is None
    assert update_kwargs["realized_rr"] is None

    metric_calls = [call.args[0] for call in mock_metrics.increment.call_args_list]
    assert "trades.winning" not in metric_calls
    assert "trades.losing" not in metric_calls
    assert "trades.breakeven" not in metric_calls


def test_handle_position_gone_still_detects_real_sl_hit():
    """Sanity check: a genuine SL hit (real price available) is unaffected."""
    manager, store, mt5_pos, bus = _manager()
    trade = _trade()

    manager._last_price[trade.entry_ticket] = 94.0  # below stop_loss=95.0 (BUY)
    store.update.return_value = trade

    with patch("src.positions.manager.metrics") as mock_metrics:
        manager._handle_position_gone(trade)

    update_kwargs = store.update.call_args.kwargs
    assert update_kwargs["close_reason"] == CloseReason.SL_HIT
    assert update_kwargs["sl_hit"] is True

    emitted_events = [call.args[0] for call in bus.emit.call_args_list]
    assert Events.TRADE_SL_HIT in emitted_events

    metric_calls = [call.args[0] for call in mock_metrics.increment.call_args_list]
    assert "trades.sl_hit" in metric_calls
    assert "trades.unknown_close" not in metric_calls
