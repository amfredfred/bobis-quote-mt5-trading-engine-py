"""Mt5Orders.open_limit_order / cancel_pending_order: request construction
and MT5 result handling. No real MT5 terminal involved — a fake `mt5`
module object stands in for client.mt5, same boundary Mt5Orders itself
talks to.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.brokers.mt5.orders import Mt5Orders
from src.brokers.mt5.types import Mt5OrderType, Mt5TradeAction, Mt5OrderTypeTime, MT5_RETCODE_DONE


class _FakeMt5:
    def __init__(self, retcode: int = MT5_RETCODE_DONE, order: int = 555, price: float = 101.5, volume: float = 0.01):
        self.retcode = retcode
        self.order = order
        self.price = price
        self.volume = volume
        self.last_request: dict | None = None

    def order_send(self, request: dict):
        self.last_request = request
        return SimpleNamespace(
            retcode=self.retcode, order=self.order, price=self.price,
            volume=self.volume, comment="ok",
        )

    def last_error(self):
        return (0, "no error")


class _FakeClient:
    def __init__(self, fake_mt5: _FakeMt5):
        self.mt5 = fake_mt5

    def ensure_connected(self) -> None:
        pass


def test_open_limit_order_sends_pending_action_with_correct_type():
    fake_mt5 = _FakeMt5()
    orders = Mt5Orders(_FakeClient(fake_mt5))

    result = orders.open_limit_order(
        symbol="XAUUSD", order_type=Mt5OrderType.BUY_LIMIT, volume=0.01,
        price=101.5, sl=99.0, tp=110.0, magic=8858, comment="test",
        expiry_seconds=900,
    )

    req = fake_mt5.last_request
    assert req["action"] == Mt5TradeAction.PENDING
    assert req["type"] == Mt5OrderType.BUY_LIMIT
    assert req["price"] == 101.5
    assert req["sl"] == 99.0
    assert req["tp"] == 110.0
    assert req["type_time"] == Mt5OrderTypeTime.SPECIFIED
    assert "expiration" in req
    assert "deviation" not in req  # no slippage concept for a resting order
    assert result.ticket == 555
    assert result.executed_price == 101.5  # the RESTING price, not a real fill


def test_open_limit_order_use_gtc_omits_expiration():
    fake_mt5 = _FakeMt5()
    orders = Mt5Orders(_FakeClient(fake_mt5))

    orders.open_limit_order(
        symbol="XAUUSD", order_type=Mt5OrderType.BUY_LIMIT, volume=0.01,
        price=101.5, sl=99.0, tp=110.0, magic=8858, comment="test",
        expiry_seconds=900, use_gtc=True,
    )

    req = fake_mt5.last_request
    assert req["type_time"] == Mt5OrderTypeTime.GTC
    assert "expiration" not in req


def test_open_limit_order_raises_on_bad_retcode():
    fake_mt5 = _FakeMt5(retcode=10015)  # INVALID_PRICE
    orders = Mt5Orders(_FakeClient(fake_mt5))

    with pytest.raises(RuntimeError, match="retcode=10015"):
        orders.open_limit_order(
            symbol="XAUUSD", order_type=Mt5OrderType.BUY_LIMIT, volume=0.01,
            price=101.5, sl=99.0, tp=110.0, magic=8858, comment="test",
            expiry_seconds=900,
        )


def test_cancel_pending_order_sends_remove_action():
    fake_mt5 = _FakeMt5()
    orders = Mt5Orders(_FakeClient(fake_mt5))

    orders.cancel_pending_order(ticket=777)

    req = fake_mt5.last_request
    assert req["action"] == Mt5TradeAction.REMOVE
    assert req["order"] == 777


def test_cancel_pending_order_raises_on_failure():
    fake_mt5 = _FakeMt5(retcode=99999)
    orders = Mt5Orders(_FakeClient(fake_mt5))

    with pytest.raises(RuntimeError):
        orders.cancel_pending_order(ticket=777)
