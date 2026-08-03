"""Test risk rules."""

from dataclasses import replace
from types import SimpleNamespace

from src.config.settings import EntryDriftConfig, RiskConfig
from src.domain.position import SymbolInfo
from src.domain.signal_interface import (
    BosDirection,
    CandlePattern,
    HtfRange,
    InboundSignal,
    LtfRange,
    RejectionCandle,
    SignalDirection,
    SignalStatus,
)
from src.domain.trade import OrderSide, TradeStatus
from src.risk.rules import (
    RuleContext,
    entry_drift_rule,
    min_rr_rule,
    no_hedging_rule,
    spread_quality_rule,
)


def test_spread_quality_uses_xauusd_threshold_override() -> None:
    ctx = _context(
        symbol="XAUUSD",
        ask=66864.0,
        bid=66850.0,
        stop_loss=66886.7,
        symbol_thresholds={"XAUUSD": 0.40},
    )

    result = spread_quality_rule(ctx)

    assert result.approved is True


def test_spread_quality_keeps_global_threshold_for_unknown_symbols() -> None:
    ctx = _context(
        symbol="UNKNOWN",
        ask=66864.0,
        bid=66850.0,
        stop_loss=66886.7,
        symbol_thresholds={"XAUUSD": 0.40},
    )

    result = spread_quality_rule(ctx)

    assert result.approved is False
    assert "0.38 > 0.25" in result.reason


def test_spread_quality_market_order_uses_live_price_for_sl_distance() -> None:
    """Live price has drifted very close to the stop (SL distance shrinks
    to almost nothing), inflating the spread/SL ratio past threshold -
    correct for a market order, which really would fill near there."""
    ctx = _context(
        symbol="XAUUSD",
        direction=SignalDirection.SHORT,
        ask=101.0,
        bid=100.9,
        entry_price=100.0,
        stop_loss=101.0,
        symbol_thresholds={},
        entry_type="market",
    )

    result = spread_quality_rule(ctx)

    assert result.approved is False


def test_spread_quality_limit_order_uses_entry_price_for_sl_distance() -> None:
    """Same live prices as the market-order case above, but this is a
    LIMIT order resting at entry_price=100.0 - its real SL distance is
    abs(100.0-101.0)=1.0, not the live-price-shrunk 0.1, so the ratio must
    be computed against entry_price and approve."""
    ctx = _context(
        symbol="XAUUSD",
        direction=SignalDirection.SHORT,
        ask=101.0,
        bid=100.9,
        entry_price=100.0,
        stop_loss=101.0,
        symbol_thresholds={},
        entry_type="limit",
    )

    result = spread_quality_rule(ctx)

    assert result.approved is True


def test_min_rr_uses_better_long_pullback_fill() -> None:
    ctx = _context(
        symbol="XAUUSD",
        direction=SignalDirection.LONG,
        ask=99.5,
        bid=99.4,
        entry_price=100.0,
        stop_loss=99.0,
        tp2=105.0,
        risk_reward_ratio=5.0,
        min_rr_ratio=6.0,
        symbol_thresholds={},
    )

    result = min_rr_rule(ctx)

    assert result.approved is True


def test_min_rr_rejects_long_fill_past_stop() -> None:
    ctx = _context(
        symbol="XAUUSD",
        direction=SignalDirection.LONG,
        ask=98.5,
        bid=98.4,
        entry_price=100.0,
        stop_loss=99.0,
        tp2=105.0,
        risk_reward_ratio=5.0,
        min_rr_ratio=1.0,
        symbol_thresholds={},
    )

    result = min_rr_rule(ctx)

    assert result.approved is False
    assert "at/below LONG stop" in result.reason


def test_min_rr_rejects_short_chase_that_loses_reward() -> None:
    ctx = _context(
        symbol="XAUUSD",
        direction=SignalDirection.SHORT,
        ask=95.8,
        bid=95.7,
        entry_price=100.0,
        stop_loss=101.0,
        tp2=95.0,
        risk_reward_ratio=5.0,
        min_rr_ratio=1.0,
        symbol_thresholds={},
    )

    result = min_rr_rule(ctx)

    assert result.approved is False
    assert "Actual R:R 0.13 < minimum 1.0" in result.reason


def test_min_rr_limit_order_ignores_live_price_drift_past_stop() -> None:
    """Same numbers as test_min_rr_rejects_long_fill_past_stop (live price
    has drifted past the stop) - but this is a LIMIT order, which rests at
    entry_price and never fills at live market price at all. Must approve:
    checking R:R against a hypothetical "buy right now at market" price is
    the exact bug that blocked real pure_crt signals live."""
    ctx = _context(
        symbol="XAUUSD",
        direction=SignalDirection.LONG,
        ask=98.5,
        bid=98.4,
        entry_price=100.0,
        stop_loss=99.0,
        tp2=105.0,
        risk_reward_ratio=5.0,
        min_rr_ratio=1.0,
        symbol_thresholds={},
        entry_type="limit",
    )

    result = min_rr_rule(ctx)

    assert result.approved is True


def test_min_rr_market_order_unaffected_still_rejects_fill_past_stop() -> None:
    """Sanity check: the fix is entry_type-scoped - a genuine market order
    with live price past the stop must still reject, exactly as before."""
    ctx = _context(
        symbol="XAUUSD",
        direction=SignalDirection.LONG,
        ask=98.5,
        bid=98.4,
        entry_price=100.0,
        stop_loss=99.0,
        tp2=105.0,
        risk_reward_ratio=5.0,
        min_rr_ratio=1.0,
        symbol_thresholds={},
        entry_type="market",
    )

    result = min_rr_rule(ctx)

    assert result.approved is False


def test_entry_drift_limit_order_never_counts_live_price_drift() -> None:
    """Same 400%-drift setup as test_entry_drift_enabled_rejects_beyond_
    threshold, but entry_type="limit": a limit order fills at entry_price
    (or not at all), so there's no live-price drift to measure until it
    actually fills - must always report 0% drift and approve, even with
    the drift check enabled."""
    ctx = _context(
        symbol="XAUUSD",
        direction=SignalDirection.LONG,
        ask=104.0,
        bid=103.9,
        entry_price=100.0,
        stop_loss=99.0,
        tp2=110.0,
        risk_reward_ratio=3.0,
        symbol_thresholds={},
        entry_drift=EntryDriftConfig(enabled=True, max_drift_pct_of_risk=25.0),
        entry_type="limit",
    )

    result = entry_drift_rule(ctx)

    assert result.approved is True
    assert result.data["entry_drift_pct_of_risk"] == 0.0


def test_entry_drift_disabled_always_approves_but_records_diagnostic() -> None:
    # LONG entry=100, stop=99 -> signal risk = 1.0. Fill at ask=104 is a 400%
    # drift, which would fail any sane threshold - but the rule is disabled
    # by default, so it must approve while still returning the measurement.
    ctx = _context(
        symbol="XAUUSD",
        direction=SignalDirection.LONG,
        ask=104.0,
        bid=103.9,
        entry_price=100.0,
        stop_loss=99.0,
        tp2=110.0,
        risk_reward_ratio=3.0,
        symbol_thresholds={},
    )

    result = entry_drift_rule(ctx)

    assert result.approved is True
    assert result.data["entry_drift_pct_of_risk"] == 400.0


def test_entry_drift_enabled_rejects_beyond_threshold() -> None:
    ctx = _context(
        symbol="XAUUSD",
        direction=SignalDirection.LONG,
        ask=104.0,
        bid=103.9,
        entry_price=100.0,
        stop_loss=99.0,
        tp2=110.0,
        risk_reward_ratio=3.0,
        symbol_thresholds={},
        entry_drift=EntryDriftConfig(enabled=True, max_drift_pct_of_risk=25.0),
    )

    result = entry_drift_rule(ctx)

    assert result.approved is False
    assert "Entry drifted 400.0% of signal risk" in result.reason
    assert result.data["entry_drift_pct_of_risk"] == 400.0


def test_entry_drift_enabled_approves_within_threshold() -> None:
    # LONG entry=100, stop=99 -> signal risk = 1.0. Fill at ask=100.1 is a
    # 10% drift, under the 25% threshold.
    ctx = _context(
        symbol="XAUUSD",
        direction=SignalDirection.LONG,
        ask=100.1,
        bid=100.0,
        entry_price=100.0,
        stop_loss=99.0,
        tp2=110.0,
        risk_reward_ratio=3.0,
        symbol_thresholds={},
        entry_drift=EntryDriftConfig(enabled=True, max_drift_pct_of_risk=25.0),
    )

    result = entry_drift_rule(ctx)

    assert result.approved is True
    assert abs(result.data["entry_drift_pct_of_risk"] - 10.0) < 1e-6


def test_entry_drift_favorable_pullback_never_counts_even_when_enabled() -> None:
    # Same pullback scenario as test_min_rr_uses_better_long_pullback_fill:
    # LONG entry=100, stop=99 -> signal risk = 1.0. Fill at ask=99.5 is
    # BELOW the signal's entry - a cheaper, more favorable LONG fill that
    # shrinks risk and grows reward. This must read as zero drift, not a
    # 50% adverse move, even with the rule enabled and a tight threshold.
    ctx = _context(
        symbol="XAUUSD",
        direction=SignalDirection.LONG,
        ask=99.5,
        bid=99.4,
        entry_price=100.0,
        stop_loss=99.0,
        tp2=105.0,
        risk_reward_ratio=5.0,
        symbol_thresholds={},
        entry_drift=EntryDriftConfig(enabled=True, max_drift_pct_of_risk=5.0),
    )

    result = entry_drift_rule(ctx)

    assert result.approved is True
    assert result.data["entry_drift_pct_of_risk"] == 0.0


def _open_trade(*, symbol: str, side: OrderSide, status: TradeStatus = TradeStatus.OPEN):
    return SimpleNamespace(id="open-1", symbol=symbol, side=side, status=status)


def test_no_hedging_rejects_opposite_side_on_aliased_symbol() -> None:
    # Regression: Trade.symbol is stored broker-resolved (planner.py sets
    # it from symbol_info.symbol - e.g. Deriv's "Crash 500 Index" for
    # canonical "CRASH500"), so the rule must compare against
    # signal.resolved_symbol, not signal.symbol, or it silently never
    # matches for any aliased symbol - which is exactly what let a hedge
    # through on CRASH500.
    ctx = _context(
        symbol="CRASH500",
        direction=SignalDirection.SHORT,
        ask=66864.0,
        bid=66850.0,
        stop_loss=66886.7,
        symbol_thresholds={},
    )
    ctx.signal = replace(ctx.signal, resolved_symbol="Crash 500 Index")
    ctx.open_trades = [
        _open_trade(symbol="Crash 500 Index", side=OrderSide.BUY),
    ]

    result = no_hedging_rule(ctx)

    assert result.approved is False
    assert "NO_HEDGING" in result.reason


def test_no_hedging_approves_same_side_on_aliased_symbol() -> None:
    ctx = _context(
        symbol="CRASH500",
        direction=SignalDirection.SHORT,
        ask=66864.0,
        bid=66850.0,
        stop_loss=66886.7,
        symbol_thresholds={},
    )
    ctx.signal = replace(ctx.signal, resolved_symbol="Crash 500 Index")
    ctx.open_trades = [
        _open_trade(symbol="Crash 500 Index", side=OrderSide.SELL),
    ]

    result = no_hedging_rule(ctx)

    assert result.approved is True


def test_no_hedging_ignores_conflict_on_a_different_symbol() -> None:
    ctx = _context(
        symbol="CRASH500",
        direction=SignalDirection.SHORT,
        ask=66864.0,
        bid=66850.0,
        stop_loss=66886.7,
        symbol_thresholds={},
    )
    ctx.signal = replace(ctx.signal, resolved_symbol="Crash 500 Index")
    ctx.open_trades = [
        _open_trade(symbol="Boom 500 Index", side=OrderSide.BUY),
    ]

    result = no_hedging_rule(ctx)

    assert result.approved is True


def _context(
    *,
    symbol: str,
    ask: float,
    bid: float,
    stop_loss: float,
    symbol_thresholds: dict[str, float],
    direction: SignalDirection = SignalDirection.SHORT,
    entry_price: float = 66855.0,
    tp2: float = 66791.6,
    risk_reward_ratio: float = 2.0,
    min_rr_ratio: float = 1.0,
    entry_drift: EntryDriftConfig | None = None,
    entry_type: str = "market",
) -> RuleContext:
    return RuleContext(
        signal=_signal(
            symbol=symbol,
            direction=direction,
            entry_price=entry_price,
            stop_loss=stop_loss,
            tp2=tp2,
            risk_reward_ratio=risk_reward_ratio,
            entry_type=entry_type,
        ),
        open_trades=[],
        config=_risk_config(
            symbol_thresholds, min_rr_ratio=min_rr_ratio, entry_drift=entry_drift
        ),
        daily_loss_pct=0.0,
        effective_open=0,
        effective_symbol=0,
        symbol_info=_symbol_info(symbol=symbol, ask=ask, bid=bid),
    )


def _risk_config(
    symbol_thresholds: dict[str, float],
    min_rr_ratio: float = 1.0,
    entry_drift: EntryDriftConfig | None = None,
) -> RiskConfig:
    return RiskConfig(
        max_losing_streak=3,
        max_daily_loss_percent=2.5,
        max_exposure_per_symbol=2,
        min_rr_ratio=min_rr_ratio,
        max_lot_size=100.0,
        min_lot_size=0.01,
        sl_ratio_threshold=0.25,
        symbol_sl_ratio_threshold=symbol_thresholds,
        symbol_risk_multiplier={},
        entry_drift=entry_drift if entry_drift is not None else EntryDriftConfig(),
    )


def _symbol_info(*, symbol: str, ask: float, bid: float) -> SymbolInfo:
    return SymbolInfo(
        symbol=symbol,
        description=symbol,
        currency_base=symbol,
        currency_profit="USD",
        currency_margin="USD",
        digits=1,
        point=1.0,
        tick_size=1.0,
        tick_value=1.0,
        contract_size=1.0,
        lot_min=0.01,
        lot_max=100.0,
        lot_step=0.01,
        ask=ask,
        bid=bid,
        spread=int(ask - bid),
        spread_float=True,
        margin_initial=0.0,
        margin_maintenance=0.0,
        margin_hedged=0.0,
        filling_mode=1,
        execution_mode=0,
        trade_mode=0,
        swap_mode=0,
        swap_long=0.0,
        swap_short=0.0,
        swap_rollover3days=3,
        stops_level=0,
        freeze_level=0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
    )


def _signal(
    *,
    symbol: str,
    direction: SignalDirection,
    entry_price: float,
    stop_loss: float,
    tp2: float,
    risk_reward_ratio: float,
    entry_type: str = "market",
) -> InboundSignal:
    is_short = direction == SignalDirection.SHORT
    return InboundSignal(
        id=f"{symbol}-test",
        symbol=symbol,
        resolved_symbol=symbol,
        direction=direction,
        status=SignalStatus.TRIGGERED,
        entry_price=entry_price,
        stop_loss=stop_loss,
        tp1=entry_price + (tp2 - entry_price) * 0.5,
        tp2=tp2,
        risk_reward_ratio=risk_reward_ratio,
        risk_pips=abs(entry_price - stop_loss),
        htf_range=HtfRange(
            range_high=66980.0,
            range_low=66790.0,
            bos_direction=BosDirection.BEARISH if is_short else BosDirection.BULLISH,
            timestamp=1,
            broken_at=1,
            tp_level=tp2,
            midpoint=66885.0,
            height=190.0,
            htf_candle_open=1,
            htf_candle_close=2,
        ),
        ltf_range=LtfRange(
            range_high=66980.0,
            range_low=66915.0,
            timestamp=1,
            direction=direction,
            sl_level=stop_loss,
        ),
        rejection_candle=RejectionCandle(
            open=66940.0,
            high=66980.0,
            low=66915.0,
            close=entry_price,
            timestamp=1,
            wick_ratio=0.3,
            pattern=CandlePattern.CRT_SELL if is_short else CandlePattern.CRT_BUY,
            wick_tip=66980.0,
        ),
        created_at=1,
        triggered_at=1,
        entry_type=entry_type,
    )
