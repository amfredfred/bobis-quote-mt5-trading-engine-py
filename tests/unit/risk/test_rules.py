"""Test risk rules."""

from src.config.settings import RiskConfig
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
from src.risk.rules import RuleContext, spread_quality_rule


def test_spread_quality_uses_symbol_threshold_override() -> None:
    ctx = _context(
        symbol="JP225",
        ask=66864.0,
        bid=66850.0,
        stop_loss=66886.7,
        symbol_thresholds={"JP225": 0.40},
    )

    result = spread_quality_rule(ctx)

    assert result.approved is True


def test_spread_quality_keeps_global_threshold_for_other_symbols() -> None:
    ctx = _context(
        symbol="XAUUSD",
        ask=66864.0,
        bid=66850.0,
        stop_loss=66886.7,
        symbol_thresholds={"JP225": 0.40},
    )

    result = spread_quality_rule(ctx)

    assert result.approved is False
    assert "0.38 > 0.25" in result.reason


def _context(
    *,
    symbol: str,
    ask: float,
    bid: float,
    stop_loss: float,
    symbol_thresholds: dict[str, float],
) -> RuleContext:
    return RuleContext(
        signal=_signal(symbol=symbol, stop_loss=stop_loss),
        open_trades=[],
        config=_risk_config(symbol_thresholds),
        daily_loss_pct=0.0,
        effective_open=0,
        effective_symbol=0,
        symbol_info=_symbol_info(symbol=symbol, ask=ask, bid=bid),
    )


def _risk_config(symbol_thresholds: dict[str, float]) -> RiskConfig:
    return RiskConfig(
        max_losing_streak=3,
        max_daily_loss_percent=2.5,
        max_exposure_per_symbol=2,
        min_rr_ratio=1.0,
        max_lot_size=100.0,
        min_lot_size=0.01,
        sl_ratio_threshold=0.25,
        symbol_sl_ratio_threshold=symbol_thresholds,
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


def _signal(*, symbol: str, stop_loss: float) -> InboundSignal:
    return InboundSignal(
        id=f"{symbol}-test",
        symbol=symbol,
        resolved_symbol=symbol,
        direction=SignalDirection.SHORT,
        status=SignalStatus.TRIGGERED,
        entry_price=66855.0,
        stop_loss=stop_loss,
        tp1=66823.3,
        tp2=66791.6,
        risk_reward_ratio=2.0,
        risk_pips=36.7,
        htf_range=HtfRange(
            range_high=66980.0,
            range_low=66790.0,
            bos_direction=BosDirection.BEARISH,
            timestamp=1,
            broken_at=1,
            tp_level=66790.0,
            midpoint=66885.0,
            height=190.0,
            htf_candle_open=1,
            htf_candle_close=2,
        ),
        ltf_range=LtfRange(
            range_high=66980.0,
            range_low=66915.0,
            timestamp=1,
            direction=SignalDirection.SHORT,
            sl_level=stop_loss,
        ),
        rejection_candle=RejectionCandle(
            open=66940.0,
            high=66980.0,
            low=66915.0,
            close=66855.0,
            timestamp=1,
            wick_ratio=0.3,
            pattern=CandlePattern.CRT_SELL,
            wick_tip=66980.0,
        ),
        created_at=1,
        triggered_at=1,
    )
