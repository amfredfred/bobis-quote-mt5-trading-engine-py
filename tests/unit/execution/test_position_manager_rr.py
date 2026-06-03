from src.domain.trade import OrderSide, Trade, TradePlan, TradeStatus
from src.positions.manager import calculate_realized_rr


def test_calculate_realized_rr_buy_manual_loss_is_negative() -> None:
    trade = _trade(OrderSide.BUY, entry=100.0, stop=95.0)

    assert calculate_realized_rr(trade, 97.5) == -0.5


def test_calculate_realized_rr_sell_manual_loss_is_negative() -> None:
    trade = _trade(OrderSide.SELL, entry=100.0, stop=105.0)

    assert calculate_realized_rr(trade, 102.5) == -0.5


def test_calculate_realized_rr_sell_profit_is_positive() -> None:
    trade = _trade(OrderSide.SELL, entry=100.0, stop=105.0)

    assert calculate_realized_rr(trade, 90.0) == 2.0


def _trade(side: OrderSide, entry: float, stop: float) -> Trade:
    plan = TradePlan(
        signal_id="sig",
        symbol="XAUUSD",
        side=side,
        entry_price=entry,
        stop_loss=stop,
        tp1=0.0,
        tp2=0.0,
        lot_size=0.01,
        risk_amount=10.0,
        risk_percent=1.0,
        risk_reward_ratio=2.0,
        planned_at=1,
        signal=None,
    )
    return Trade(
        id="trade",
        signal_id="sig",
        symbol="XAUUSD",
        side=side,
        status=TradeStatus.OPEN,
        plan=plan,
        entry_price=entry,
        stop_loss=stop,
    )
