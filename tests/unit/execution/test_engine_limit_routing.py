"""ExecutionEngine.execute(): entry_type routing between the market and
limit paths, and the "skip, no market fallback" behavior when limit
placement fails (e.g. price already moved past the requested level)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from src.core.events import Events
from src.domain.position import AccountInfo, SymbolInfo
from src.domain.signal_interface import (
    BosDirection,
    CandlePattern,
    HtfRange,
    InboundSignal,
    RejectionCandle,
    SignalDirection,
    SignalStatus,
)
from src.execution.engine import ExecutionEngine
from src.utils.time import now_ms


def _signal(entry_type: str = "market", limit_expiry_seconds: int | None = None) -> InboundSignal:
    ts = now_ms()
    return InboundSignal(
        id="sig-limit-1",
        symbol="XAU/USD",
        resolved_symbol="XAUUSD",
        direction=SignalDirection.LONG,
        status=SignalStatus.TRIGGERED,
        entry_price=2000.0,
        stop_loss=1995.0,
        tp1=2010.0,
        tp2=2020.0,
        risk_reward_ratio=4.0,
        risk_pips=5.0,
        htf_range=HtfRange(
            range_high=2020.0, range_low=1990.0, bos_direction=BosDirection.BULLISH,
            timestamp=ts, broken_at=ts, tp_level=2020.0, midpoint=2005.0, height=30.0,
            htf_candle_open=ts, htf_candle_close=ts + 60_000,
        ),
        rejection_candle=RejectionCandle(
            open=1998.0, high=2001.0, low=1995.0, close=2000.0, timestamp=ts,
            wick_ratio=0.5, pattern=CandlePattern.FVG_LONG, wick_tip=1995.0,
        ),
        created_at=ts,
        setup_candle_close_at=ts - 1_000,
        emitted_at=ts - 900,
        received_at=ts - 800,
        entry_type=entry_type,
        limit_expiry_seconds=limit_expiry_seconds,
    )


def _engine(orders: "_Orders", pending_manager: "_PendingOrderManager") -> ExecutionEngine:
    return ExecutionEngine(
        risk_engine=_Risk(),
        trade_planner=_Planner(),
        order_manager=orders,
        mt5_orders=MagicMock(),
        pending_order_manager=pending_manager,
        mt5_positions=_Positions(),
        position_store=_Store(),
        trade_repo=_Repo(),
        event_bus=_Bus(),
        exec_config=_execution_config(),
    )


def test_limit_entry_type_routes_to_execute_limit_order_not_market():
    orders = _Orders()
    pending_manager = _PendingOrderManager()
    engine = _engine(orders, pending_manager)

    result = engine.execute(_signal(entry_type="limit", limit_expiry_seconds=900))

    assert orders.market_calls == 0
    assert orders.limit_calls == 1
    assert result is None  # no Trade yet — PendingOrderManager owns the fill
    assert len(pending_manager.tracked) == 1
    assert pending_manager.tracked[0].plan.symbol == "XAUUSD"


def test_limit_expiry_falls_back_to_config_when_signal_omits_it():
    orders = _Orders()
    pending_manager = _PendingOrderManager()
    engine = _engine(orders, pending_manager)

    engine.execute(_signal(entry_type="limit", limit_expiry_seconds=None))

    assert orders.last_expiry_seconds == 1800  # _execution_config()'s default


def test_limit_placement_failure_skips_trade_no_market_fallback():
    orders = _Orders(fail_limit=True)
    pending_manager = _PendingOrderManager()
    engine = _engine(orders, pending_manager)

    bus = engine._bus  # the _Bus() instance built inside _engine
    result = engine.execute(_signal(entry_type="limit", limit_expiry_seconds=900))

    assert result is None
    assert orders.market_calls == 0  # never falls back to market
    assert pending_manager.tracked == []
    assert any(
        event == Events.TRADE_ERROR and payload.get("reason") == "limit_order_placement_failed"
        for event, payload in bus.events
    )


def test_market_entry_type_unaffected_by_new_routing():
    orders = _Orders()
    pending_manager = _PendingOrderManager()
    engine = _engine(orders, pending_manager)

    trade = engine.execute(_signal(entry_type="market"))

    assert trade is not None
    assert orders.market_calls == 1
    assert orders.limit_calls == 0
    assert pending_manager.tracked == []


class _Bus:
    def __init__(self) -> None:
        self.events = []

    def emit(self, event: str, payload=None) -> None:
        self.events.append((event, payload))


class _Risk:
    def evaluate(self, *args, **kwargs):
        return SimpleNamespace(approved=True, reason=None, data={}, risk_multiplier=1.0)


class _Orders:
    def __init__(self, fail_limit: bool = False) -> None:
        self.market_calls = 0
        self.limit_calls = 0
        self.last_expiry_seconds: int | None = None
        self._fail_limit = fail_limit

    def execute_market_order(self, *args, **kwargs):
        self.market_calls += 1
        return 1, 2000.0, 0.01

    def execute_limit_order(self, plan, symbol_info, expiry_seconds, tp_override=None, comment=None):
        self.limit_calls += 1
        self.last_expiry_seconds = expiry_seconds
        if self._fail_limit:
            raise RuntimeError("order_send failed: retcode=10015 comment=Invalid price")
        return 42, plan.entry_price


class _PendingOrderManager:
    def __init__(self) -> None:
        self.tracked = []

    def track(self, record) -> None:
        self.tracked.append(record)


class _Planner:
    def plan(self, signal: InboundSignal, *args, **kwargs):
        from src.domain.trade import OrderSide, TradePlan

        side = OrderSide.BUY if signal.direction == SignalDirection.LONG else OrderSide.SELL
        return TradePlan(
            signal_id=signal.id,
            symbol=signal.resolved_symbol or signal.symbol,
            side=side,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            tp1=signal.tp1,
            tp2=signal.tp2,
            lot_size=0.01,
            risk_amount=10.0,
            risk_percent=1.0,
            risk_reward_ratio=signal.risk_reward_ratio,
            planned_at=now_ms(),
            signal=signal,
        )


class _Positions:
    def get_account_info(self) -> AccountInfo:
        return AccountInfo(
            login=1, server="demo", currency="USD", balance=10_000.0, equity=10_000.0,
            margin=0.0, free_margin=10_000.0, margin_level=0.0, leverage=100,
        )

    def get_symbol_info(self, symbol: str) -> SymbolInfo:
        return SymbolInfo(
            symbol=symbol, description=symbol, currency_base="XAU", currency_profit="USD",
            currency_margin="USD", digits=2, point=0.01, tick_size=0.01, tick_value=1.0,
            contract_size=100.0, lot_min=0.01, lot_max=100.0, lot_step=0.01,
            ask=2000.05, bid=2000.00, spread=5, spread_float=True,
            margin_initial=0.0, margin_maintenance=0.0, margin_hedged=0.0,
            filling_mode=1, execution_mode=0, trade_mode=0, swap_mode=0,
            swap_long=0.0, swap_short=0.0, swap_rollover3days=3,
            stops_level=0, freeze_level=0, volume_min=0.01, volume_max=100.0, volume_step=0.01,
        )


class _Store:
    def __init__(self) -> None:
        self.added = []

    def get_by_signal_id(self, signal_id: str):
        return None

    def get_open_trades(self):
        return list(self.added)

    def add(self, trade):
        self.added.append(trade)


class _Repo:
    def save(self, trade):
        return True


def _execution_config():
    from src.config.settings import ExecutionConfig

    return ExecutionConfig(
        tp1_trigger_pct=55.0, tp1_percentage=0.0, move_sl_to_be_on_tp1=True,
        slippage=10, magic=8858, spread_risk_multiplier=1.0, order_retry_count=2,
        max_entry_slippage_pct_of_stop=0.2, close_on_slippage_exceed=False,
        order_retry_delay_sec=0.5, max_signal_age_ms=90_000,
    )
