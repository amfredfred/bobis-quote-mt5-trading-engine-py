"""ExecutionEngine.execute(): reject a new same-symbol+direction signal
outright when the existing open position on that symbol+direction hasn't
moved favorably past its own entry yet — near-entry and underwater both
block, only genuine profit unlocks stacking a second trade."""

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
from src.domain.trade import OrderSide, Trade, TradePlan, TradeStatus
from src.execution.engine import ExecutionEngine
from src.utils.time import now_ms


def _signal(direction: SignalDirection = SignalDirection.LONG) -> InboundSignal:
    ts = now_ms()
    return InboundSignal(
        id="sig-stack-1",
        symbol="XAU/USD",
        resolved_symbol="XAUUSD",
        direction=direction,
        status=SignalStatus.TRIGGERED,
        entry_price=2000.0,
        stop_loss=1995.0 if direction == SignalDirection.LONG else 2005.0,
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
        entry_type="market",
    )


def _existing_trade(entry_price: float, side: OrderSide = OrderSide.BUY) -> Trade:
    plan = TradePlan(
        signal_id="sig-earlier", symbol="XAUUSD", side=side, entry_price=entry_price,
        stop_loss=1990.0, tp1=2010.0, tp2=2020.0, lot_size=0.01, risk_amount=10.0,
        risk_percent=1.0, risk_reward_ratio=4.0, planned_at=1, signal=None,
    )
    return Trade(
        id="earlier-trade", signal_id="sig-earlier", symbol="XAUUSD", side=side,
        status=TradeStatus.OPEN, plan=plan, entry_ticket=1, entry_price=entry_price,
        stop_loss=1990.0, tp1=2010.0, tp2=2020.0,
    )


def _engine(store: "_Store", positions: "_Positions | None" = None) -> ExecutionEngine:
    return ExecutionEngine(
        risk_engine=_Risk(),
        trade_planner=_Planner(),
        order_manager=_Orders(),
        mt5_orders=MagicMock(),
        pending_order_manager=_PendingOrderManager(),
        mt5_positions=positions or _Positions(),
        position_store=store,
        trade_repo=_Repo(),
        event_bus=_Bus(),
        exec_config=_execution_config(),
    )


def test_rejects_new_signal_when_existing_position_at_a_loss():
    # BUY existing trade entered at 2010; live bid is 2000.00 (below entry) — losing.
    store = _Store(open_trades=[_existing_trade(entry_price=2010.0)])
    engine = _engine(store, _Positions(bid=2000.00, ask=2000.05))
    bus = engine._bus

    result = engine.execute(_signal(SignalDirection.LONG))

    assert result is None
    assert store.added == []
    assert any(
        event == Events.RISK_REJECTED and payload.get("reason") == "existing_position_not_profitable"
        for event, payload in bus.events
    )


def test_rejects_new_signal_when_existing_position_near_entry():
    # BUY existing trade entered at 2000.00; live bid is 2000.00 — no movement, not profitable.
    store = _Store(open_trades=[_existing_trade(entry_price=2000.00)])
    engine = _engine(store, _Positions(bid=2000.00, ask=2000.05))

    result = engine.execute(_signal(SignalDirection.LONG))

    assert result is None
    assert store.added == []


def test_allows_new_signal_when_existing_position_is_profitable():
    # BUY existing trade entered at 1990; live bid is 2000.00 (above entry) — profitable.
    store = _Store(open_trades=[_existing_trade(entry_price=1990.0)])
    engine = _engine(store, _Positions(bid=2000.00, ask=2000.05))

    result = engine.execute(_signal(SignalDirection.LONG))

    assert result is not None
    assert len(store.added) == 1


def test_allows_new_signal_when_no_existing_position():
    store = _Store(open_trades=[])
    engine = _engine(store)

    result = engine.execute(_signal(SignalDirection.LONG))

    assert result is not None


def test_ignores_existing_position_on_different_symbol():
    existing = _existing_trade(entry_price=2010.0)
    existing.symbol = "US100"  # different symbol, same direction/loss — should not block
    store = _Store(open_trades=[existing])
    engine = _engine(store, _Positions(bid=2000.00, ask=2000.05))

    result = engine.execute(_signal(SignalDirection.LONG))

    assert result is not None


def test_ignores_existing_position_in_opposite_direction():
    # Existing SELL at a "loss" for a SELL (price above entry) shouldn't
    # block a new LONG signal — different direction entirely.
    existing = _existing_trade(entry_price=1990.0, side=OrderSide.SELL)
    store = _Store(open_trades=[existing])
    engine = _engine(store, _Positions(bid=2000.00, ask=2000.05))

    result = engine.execute(_signal(SignalDirection.LONG))

    assert result is not None


class _Bus:
    def __init__(self) -> None:
        self.events = []

    def emit(self, event: str, payload=None) -> None:
        self.events.append((event, payload))


class _Risk:
    def evaluate(self, *args, **kwargs):
        return SimpleNamespace(approved=True, reason=None, data={}, risk_multiplier=1.0)


class _Orders:
    def execute_market_order(self, *args, **kwargs):
        return 1, 2000.0, 0.01


class _PendingOrderManager:
    def track(self, record) -> None:
        pass


class _Planner:
    def plan(self, signal: InboundSignal, *args, **kwargs):
        side = OrderSide.BUY if signal.direction == SignalDirection.LONG else OrderSide.SELL
        return TradePlan(
            signal_id=signal.id, symbol=signal.resolved_symbol or signal.symbol, side=side,
            entry_price=signal.entry_price, stop_loss=signal.stop_loss, tp1=signal.tp1,
            tp2=signal.tp2, lot_size=0.01, risk_amount=10.0, risk_percent=1.0,
            risk_reward_ratio=signal.risk_reward_ratio, planned_at=now_ms(), signal=signal,
        )


class _Positions:
    def __init__(self, ask: float = 2000.05, bid: float = 2000.00) -> None:
        self.ask = ask
        self.bid = bid

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
            ask=self.ask, bid=self.bid, spread=5, spread_float=True,
            margin_initial=0.0, margin_maintenance=0.0, margin_hedged=0.0,
            filling_mode=1, execution_mode=0, trade_mode=0, swap_mode=0,
            swap_long=0.0, swap_short=0.0, swap_rollover3days=3,
            stops_level=0, freeze_level=0, volume_min=0.01, volume_max=100.0, volume_step=0.01,
        )


class _Store:
    def __init__(self, open_trades: list[Trade] | None = None) -> None:
        self._open_trades = open_trades or []
        self.added = []

    def get_by_signal_id(self, signal_id: str):
        return None

    def get_open_trades(self):
        return list(self._open_trades) + list(self.added)

    def add(self, trade):
        self.added.append(trade)

    def update(self, trade_id, **kwargs):
        # Only reached by the SL cascade when the pre-existing trade is
        # already profitable enough to pass the gate — not under test here.
        return None


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
