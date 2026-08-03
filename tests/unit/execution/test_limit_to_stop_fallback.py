"""OrderManager.execute_limit_order: use_limit_to_stop_fallback — when a
resting limit order is rejected because price already moved past it
(retcode=10015 INVALID_PRICE), retry once as the equivalent stop order at
the same price/SL/TP/expiry instead of skipping the signal outright."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.brokers.mt5.types import Mt5OrderType, OrderResult
from src.config.settings import ExecutionConfig
from src.domain.position import SymbolInfo
from src.domain.trade import OrderSide, TradePlan
from src.execution.order_manager import OrderManager
from src.utils.time import now_ms


def _plan(side: OrderSide = OrderSide.SELL) -> TradePlan:
    return TradePlan(
        signal_id="sig-1",
        symbol="USDJPYz",
        side=side,
        entry_price=156.487,
        stop_loss=156.512,
        tp1=156.42,
        tp2=156.364,
        lot_size=0.3,
        risk_amount=10.0,
        risk_percent=1.0,
        risk_reward_ratio=5.0,
        planned_at=now_ms(),
        signal=None,
    )


def _symbol_info() -> SymbolInfo:
    return SymbolInfo(
        symbol="USDJPYz", description="USDJPYz", currency_base="USD", currency_profit="JPY",
        currency_margin="USD", digits=3, point=0.001, tick_size=0.001, tick_value=1.0,
        contract_size=100_000.0, lot_min=0.01, lot_max=100.0, lot_step=0.01,
        ask=156.49, bid=156.48, spread=1, spread_float=True,
        margin_initial=0.0, margin_maintenance=0.0, margin_hedged=0.0,
        filling_mode=1, execution_mode=0, trade_mode=0, swap_mode=0,
        swap_long=0.0, swap_short=0.0, swap_rollover3days=3,
        stops_level=0, freeze_level=0, volume_min=0.01, volume_max=100.0, volume_step=0.01,
    )


def _config(use_fallback: bool) -> ExecutionConfig:
    return ExecutionConfig(
        tp1_trigger_pct=55.0, tp1_percentage=45.0, move_sl_to_be_on_tp1=True,
        slippage=10, magic=8858, spread_risk_multiplier=1.0, order_retry_count=0,
        max_entry_slippage_pct_of_stop=0.2, close_on_slippage_exceed=False,
        order_retry_delay_sec=0.0, max_signal_age_ms=120_000,
        use_limit_to_stop_fallback=use_fallback,
    )


def _manager(use_fallback: bool, orders: MagicMock) -> OrderManager:
    return OrderManager(mt5_orders=orders, mt5_positions=MagicMock(), exec_config=_config(use_fallback))


def test_retries_as_stop_order_at_same_level_on_invalid_price():
    orders = MagicMock()
    orders.open_limit_order.side_effect = [
        RuntimeError("order_send failed: retcode=10015 comment=Invalid price"),
        OrderResult(ticket=99, executed_price=156.487, volume=0.3, retcode=10009, comment="done"),
    ]
    manager = _manager(use_fallback=True, orders=orders)

    ticket, price = manager.execute_limit_order(_plan(), _symbol_info(), expiry_seconds=10800)

    assert ticket == 99
    assert price == 156.487
    assert orders.open_limit_order.call_count == 2

    first_call, second_call = orders.open_limit_order.call_args_list
    # Same price/SL/TP/expiry on the retry — only the order type changes.
    assert second_call.kwargs["price"] == first_call.kwargs["price"] == 156.487
    assert second_call.kwargs["sl"] == first_call.kwargs["sl"]
    assert second_call.kwargs["tp"] == first_call.kwargs["tp"]
    assert second_call.kwargs["expiry_seconds"] == first_call.kwargs["expiry_seconds"]
    assert first_call.kwargs["order_type"] == Mt5OrderType.SELL_LIMIT
    assert second_call.kwargs["order_type"] == Mt5OrderType.SELL_STOP


def test_buy_limit_falls_back_to_buy_stop():
    orders = MagicMock()
    orders.open_limit_order.side_effect = [
        RuntimeError("order_send failed: retcode=10015 comment=Invalid price"),
        OrderResult(ticket=100, executed_price=100.0, volume=0.1, retcode=10009, comment="done"),
    ]
    manager = _manager(use_fallback=True, orders=orders)

    manager.execute_limit_order(_plan(side=OrderSide.BUY), _symbol_info(), expiry_seconds=900)

    second_call = orders.open_limit_order.call_args_list[1]
    assert second_call.kwargs["order_type"] == Mt5OrderType.BUY_STOP


def test_fallback_disabled_skips_as_before():
    orders = MagicMock()
    orders.open_limit_order.side_effect = RuntimeError(
        "order_send failed: retcode=10015 comment=Invalid price"
    )
    manager = _manager(use_fallback=False, orders=orders)

    with pytest.raises(RuntimeError, match="10015"):
        manager.execute_limit_order(_plan(), _symbol_info(), expiry_seconds=10800)

    assert orders.open_limit_order.call_count == 1  # never attempted the stop retry


def test_stop_fallback_also_failing_raises_and_is_still_skipped():
    orders = MagicMock()
    orders.open_limit_order.side_effect = [
        RuntimeError("order_send failed: retcode=10015 comment=Invalid price"),
        RuntimeError("order_send failed: retcode=10015 comment=Invalid price"),
    ]
    manager = _manager(use_fallback=True, orders=orders)

    with pytest.raises(RuntimeError, match="10015"):
        manager.execute_limit_order(_plan(), _symbol_info(), expiry_seconds=10800)

    assert orders.open_limit_order.call_count == 2


def test_other_rejection_reasons_never_trigger_stop_fallback():
    """A non-INVALID_PRICE terminal rejection (e.g. margin) must not attempt
    the stop-order fallback at all - it's specific to price movement."""
    orders = MagicMock()
    orders.open_limit_order.side_effect = RuntimeError(
        "order_send failed: retcode=10019 comment=No money"
    )
    manager = _manager(use_fallback=True, orders=orders)

    with pytest.raises(RuntimeError, match="10019"):
        manager.execute_limit_order(_plan(), _symbol_info(), expiry_seconds=10800)

    assert orders.open_limit_order.call_count == 1
