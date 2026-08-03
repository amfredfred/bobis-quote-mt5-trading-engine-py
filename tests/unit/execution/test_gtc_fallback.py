"""OrderManager.execute_limit_order: use_gtc_fallback_on_invalid_expiration
— when a resting order is rejected with retcode=10022 INVALID_EXPIRATION
(seen live on XAUUSD), retry once as GTC (no broker-side expiration) at the
same price/SL/TP instead of skipping the signal outright."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.brokers.mt5.types import Mt5OrderType, OrderResult
from src.config.settings import ExecutionConfig
from src.domain.position import SymbolInfo
from src.domain.trade import OrderSide, TradePlan
from src.execution.order_manager import OrderManager
from src.utils.time import now_ms


def _plan(side: OrderSide = OrderSide.BUY) -> TradePlan:
    return TradePlan(
        signal_id="sig-1",
        symbol="XAUUSD",
        side=side,
        entry_price=4063.9,
        stop_loss=4062.43,
        tp1=4065.37,
        tp2=4071.25,
        lot_size=0.01,
        risk_amount=2.68,
        risk_percent=1.31,
        risk_reward_ratio=5.0,
        planned_at=now_ms(),
        signal=None,
    )


def _symbol_info() -> SymbolInfo:
    return SymbolInfo(
        symbol="XAUUSD", description="XAUUSD", currency_base="XAU", currency_profit="USD",
        currency_margin="USD", digits=2, point=0.01, tick_size=0.01, tick_value=1.0,
        contract_size=100.0, lot_min=0.01, lot_max=100.0, lot_step=0.01,
        ask=4064.2, bid=4063.9, spread=30, spread_float=True,
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
        use_gtc_fallback_on_invalid_expiration=use_fallback,
    )


def _manager(use_fallback: bool, orders: MagicMock) -> OrderManager:
    return OrderManager(mt5_orders=orders, mt5_positions=MagicMock(), exec_config=_config(use_fallback))


def test_retries_as_gtc_at_same_level_on_invalid_expiration():
    orders = MagicMock()
    orders.open_limit_order.side_effect = [
        RuntimeError("order_send failed: retcode=10022 comment=Invalid expiration"),
        OrderResult(ticket=77, executed_price=4063.9, volume=0.01, retcode=10009, comment="done"),
    ]
    manager = _manager(use_fallback=True, orders=orders)

    ticket, price = manager.execute_limit_order(_plan(), _symbol_info(), expiry_seconds=10800)

    assert ticket == 77
    assert price == 4063.9
    assert orders.open_limit_order.call_count == 2

    first_call, second_call = orders.open_limit_order.call_args_list
    assert first_call.kwargs.get("use_gtc", False) is False
    assert second_call.kwargs["use_gtc"] is True
    # Same order type/price/SL/TP - only the expiration handling changes.
    assert second_call.kwargs["order_type"] == first_call.kwargs["order_type"] == Mt5OrderType.BUY_LIMIT
    assert second_call.kwargs["price"] == first_call.kwargs["price"] == 4063.9
    assert second_call.kwargs["sl"] == first_call.kwargs["sl"]
    assert second_call.kwargs["tp"] == first_call.kwargs["tp"]


def test_fallback_disabled_skips_as_before():
    orders = MagicMock()
    orders.open_limit_order.side_effect = RuntimeError(
        "order_send failed: retcode=10022 comment=Invalid expiration"
    )
    manager = _manager(use_fallback=False, orders=orders)

    with pytest.raises(RuntimeError, match="10022"):
        manager.execute_limit_order(_plan(), _symbol_info(), expiry_seconds=10800)

    assert orders.open_limit_order.call_count == 1


def test_gtc_fallback_also_failing_raises_and_is_still_skipped():
    orders = MagicMock()
    orders.open_limit_order.side_effect = [
        RuntimeError("order_send failed: retcode=10022 comment=Invalid expiration"),
        RuntimeError("order_send failed: retcode=10022 comment=Invalid expiration"),
    ]
    manager = _manager(use_fallback=True, orders=orders)

    with pytest.raises(RuntimeError, match="10022"):
        manager.execute_limit_order(_plan(), _symbol_info(), expiry_seconds=10800)

    assert orders.open_limit_order.call_count == 2


def test_other_rejection_reasons_never_trigger_gtc_fallback():
    orders = MagicMock()
    orders.open_limit_order.side_effect = RuntimeError(
        "order_send failed: retcode=10015 comment=Invalid price"
    )
    manager = _manager(use_fallback=True, orders=orders)

    with pytest.raises(RuntimeError, match="10015"):
        manager.execute_limit_order(_plan(), _symbol_info(), expiry_seconds=10800)

    # Falls through to the limit-to-stop path instead (default on), not GTC.
    assert orders.open_limit_order.call_count == 2
    second_call = orders.open_limit_order.call_args_list[1]
    assert second_call.kwargs.get("use_gtc", False) is False
