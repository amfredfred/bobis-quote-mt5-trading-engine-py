"""cascade_sl_to_stacked_position: same-symbol+direction SL cascade.

Explicit user directive: match on symbol + direction ONLY, no zone/strategy
tracking. A later trade's (necessarily tighter) SL protects an earlier
still-open trade on the same symbol+direction.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.domain.trade import OrderSide, Trade, TradePlan, TradeStatus
from src.positions.sl_cascade import cascade_sl_to_stacked_position
from src.positions.store import PositionStore


def _plan(symbol: str, side: OrderSide, sl: float) -> TradePlan:
    return TradePlan(
        signal_id="sig",
        symbol=symbol,
        side=side,
        entry_price=100.0,
        stop_loss=sl,
        tp1=105.0,
        tp2=110.0,
        lot_size=0.01,
        risk_amount=10.0,
        risk_percent=1.0,
        risk_reward_ratio=3.0,
        planned_at=1,
        signal=None,
    )


def _trade(id: str, symbol: str, side: OrderSide, sl: float, ticket: int) -> Trade:
    plan = _plan(symbol, side, sl)
    return Trade(
        id=id,
        signal_id="sig",
        symbol=symbol,
        side=side,
        status=TradeStatus.OPEN,
        plan=plan,
        entry_ticket=ticket,
        entry_price=100.0,
        stop_loss=sl,
        tp1=105.0,
        tp2=110.0,
    )


def test_cascades_sl_when_new_trade_is_more_favorable_buy():
    store = PositionStore()
    earlier = _trade("earlier", "XAUUSD", OrderSide.BUY, sl=95.0, ticket=1)
    store.add(earlier)
    new_trade = _trade("new", "XAUUSD", OrderSide.BUY, sl=99.0, ticket=2)  # tighter SL

    mt5_orders = MagicMock()
    cascade_sl_to_stacked_position(store, mt5_orders, new_trade)

    mt5_orders.modify_position.assert_called_once_with(ticket=1, sl=99.0, tp=110.0)
    assert store.get("earlier").stop_loss == 99.0


def test_cascades_sl_when_new_trade_is_more_favorable_sell():
    store = PositionStore()
    earlier = _trade("earlier", "XAUUSD", OrderSide.SELL, sl=105.0, ticket=1)
    store.add(earlier)
    new_trade = _trade("new", "XAUUSD", OrderSide.SELL, sl=101.0, ticket=2)  # tighter SL

    mt5_orders = MagicMock()
    cascade_sl_to_stacked_position(store, mt5_orders, new_trade)

    mt5_orders.modify_position.assert_called_once_with(ticket=1, sl=101.0, tp=110.0)
    assert store.get("earlier").stop_loss == 101.0


def test_does_not_cascade_when_new_sl_is_worse():
    store = PositionStore()
    earlier = _trade("earlier", "XAUUSD", OrderSide.BUY, sl=99.0, ticket=1)
    store.add(earlier)
    new_trade = _trade("new", "XAUUSD", OrderSide.BUY, sl=95.0, ticket=2)  # worse SL

    mt5_orders = MagicMock()
    cascade_sl_to_stacked_position(store, mt5_orders, new_trade)

    mt5_orders.modify_position.assert_not_called()
    assert store.get("earlier").stop_loss == 99.0


def test_does_not_cascade_across_different_symbols():
    store = PositionStore()
    earlier = _trade("earlier", "US100", OrderSide.BUY, sl=95.0, ticket=1)
    store.add(earlier)
    new_trade = _trade("new", "XAUUSD", OrderSide.BUY, sl=99.0, ticket=2)

    mt5_orders = MagicMock()
    cascade_sl_to_stacked_position(store, mt5_orders, new_trade)

    mt5_orders.modify_position.assert_not_called()


def test_does_not_cascade_across_different_directions():
    store = PositionStore()
    earlier = _trade("earlier", "XAUUSD", OrderSide.SELL, sl=105.0, ticket=1)
    store.add(earlier)
    new_trade = _trade("new", "XAUUSD", OrderSide.BUY, sl=99.0, ticket=2)

    mt5_orders = MagicMock()
    cascade_sl_to_stacked_position(store, mt5_orders, new_trade)

    mt5_orders.modify_position.assert_not_called()


def test_does_not_cascade_to_itself():
    store = PositionStore()
    new_trade = _trade("new", "XAUUSD", OrderSide.BUY, sl=99.0, ticket=2)
    store.add(new_trade)

    mt5_orders = MagicMock()
    cascade_sl_to_stacked_position(store, mt5_orders, new_trade)

    mt5_orders.modify_position.assert_not_called()


def test_cascades_to_multiple_earlier_trades():
    store = PositionStore()
    store.add(_trade("earlier1", "XAUUSD", OrderSide.BUY, sl=95.0, ticket=1))
    store.add(_trade("earlier2", "XAUUSD", OrderSide.BUY, sl=96.0, ticket=2))
    new_trade = _trade("new", "XAUUSD", OrderSide.BUY, sl=99.0, ticket=3)

    mt5_orders = MagicMock()
    cascade_sl_to_stacked_position(store, mt5_orders, new_trade)

    assert mt5_orders.modify_position.call_count == 2
    assert store.get("earlier1").stop_loss == 99.0
    assert store.get("earlier2").stop_loss == 99.0


def test_modify_failure_is_logged_not_raised():
    store = PositionStore()
    earlier = _trade("earlier", "XAUUSD", OrderSide.BUY, sl=95.0, ticket=1)
    store.add(earlier)
    new_trade = _trade("new", "XAUUSD", OrderSide.BUY, sl=99.0, ticket=2)

    mt5_orders = MagicMock()
    mt5_orders.modify_position.side_effect = RuntimeError("broker rejected")

    cascade_sl_to_stacked_position(store, mt5_orders, new_trade)  # must not raise

    # Earlier trade's SL stays at its prior (still valid) value — not left
    # in an unprotected/half-updated state.
    assert store.get("earlier").stop_loss == 95.0
