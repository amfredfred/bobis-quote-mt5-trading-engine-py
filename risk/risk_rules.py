"""
risk/risk_rules.py — individual risk rules.

Each rule is a callable:  rule(ctx: RuleContext) -> RuleResult

RuleContext carries everything a rule needs — no global state reads.
Rules are composable: add to ALL_RULES without touching RiskEngine.

Guard rules:
    loss_guard_rule — delegates to LossTracker which tracks daily loss %:
        When MAX_DAILY_LOSS_PERCENT is reached, trading is paused until midnight.

    If ctx.loss_tracker is None (backward compat / tests), guard rules pass.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence, TYPE_CHECKING

from interfaces.signal_interface import InboundSignal, SignalDirection
from interfaces.trade import Trade, OrderSide, TradeStatus
from interfaces.position import SymbolInfo
from config.config import RiskConfig
from utils.price_utils import pip_size


if TYPE_CHECKING:
    from risk.loss_tracker import LossTracker


@dataclass
class RuleContext:
    signal: InboundSignal
    open_trades: List[Trade]
    config: RiskConfig
    daily_loss_pct: float
    effective_open: int
    effective_symbol: int
    symbol_info: SymbolInfo
    loss_tracker: Optional["LossTracker"] = field(default=None)


@dataclass(frozen=True)
class RuleResult:
    approved: bool
    reason: str = ""


RiskRule = Callable[[RuleContext], RuleResult]


# ── Existing rules ─────────────────────────────────────────────────────────────


def min_rr_rule(ctx: RuleContext) -> RuleResult:
    if ctx.signal.risk_reward_ratio < ctx.config.min_rr_ratio:
        return RuleResult(
            approved=False,
            reason=f"R:R {ctx.signal.risk_reward_ratio:.2f} < minimum {ctx.config.min_rr_ratio}",
        )
    return RuleResult(approved=True)


def max_open_trades_rule(ctx: RuleContext) -> RuleResult:
    if ctx.effective_open >= ctx.config.max_open_trades:
        return RuleResult(
            approved=False,
            reason=f"Max open trades reached ({ctx.effective_open}/{ctx.config.max_open_trades})",
        )
    return RuleResult(approved=True)


def max_symbol_exposure_rule(ctx: RuleContext) -> RuleResult:
    if ctx.effective_symbol >= ctx.config.max_exposure_per_symbol:
        return RuleResult(
            approved=False,
            reason=(
                f"Symbol exposure limit for {ctx.signal.symbol}: "
                f"{ctx.effective_symbol}/{ctx.config.max_exposure_per_symbol}"
            ),
        )
    return RuleResult(approved=True)


def duplicate_signal_rule(ctx: RuleContext) -> RuleResult:
    duplicate = next(
        (
            t
            for t in ctx.open_trades
            if t.signal_id == ctx.signal.id and t.signal_id != "unknown"
        ),
        None,
    )
    if duplicate:
        return RuleResult(
            approved=False,
            reason=f"Duplicate signal: trade {duplicate.id} already open for {ctx.signal.id}",
        )
    return RuleResult(approved=True)


def daily_loss_limit_rule(ctx: RuleContext) -> RuleResult:
    """Monetary daily drawdown guard — sourced from MT5 account equity (start-of-day basis).

    Two layers of protection that work with MAX_OPEN_TRADES to ensure the
    configured limit is never fully hit:

    Layer 1 — Hard safety stop at 95 % of MAX_DAILY_LOSS_PERCENT.
        New trades are refused once the realised loss reaches 95 % of the
        configured limit.  This leaves a 5 % buffer so that any trades already
        open at the time of the stop cannot push the account past 100 % of the
        daily limit even if they all hit SL simultaneously.

    Layer 2 — Pre-trade budget projection (percentage mode only).
        Before opening a trade we check whether the risk budget for that single
        trade, added to what has already been lost today, would exceed the 95 %
        safety threshold.  This links MAX_OPEN_TRADES and MAX_DAILY_LOSS_PERCENT:
        with 5 trades at 1 % risk each and a 5 % daily limit the engine will
        naturally stop accepting new trades once 4 % has been realised, because
        the next 1 % would cross 95 % of 5 % (= 4.75 %).

        Example (MAX_DAILY_LOSS_PERCENT=5, RISK_PERCENT_PER_TRADE=1):
            safety_threshold = 4.75 %
            daily_loss_pct=3.8 % → 3.8 + 1.0 = 4.8 > 4.75 → REJECTED
            daily_loss_pct=3.7 % → 3.7 + 1.0 = 4.7 < 4.75 → ALLOWED
    """
    budget = ctx.config.max_daily_loss_percent
    safety_threshold = budget * 0.95  # never let the engine reach the full limit

    # Layer 1 — hard stop: already at or past 95 %
    if ctx.daily_loss_pct >= safety_threshold:
        return RuleResult(
            approved=False,
            reason=(
                f"Daily loss safety stop: {ctx.daily_loss_pct:.2f}% >= "
                f"{safety_threshold:.2f}% (95% of {budget}% limit)"
            ),
        )

    # Layer 2 — budget projection: would this trade's SL push us past the threshold?
    from utils.lot_calculator import RiskMode as _RiskMode

    if ctx.config.risk_mode == _RiskMode.PERCENTAGE:
        per_trade_risk = ctx.config.risk_percent_per_trade
        projected = ctx.daily_loss_pct + per_trade_risk
        if projected > safety_threshold:
            return RuleResult(
                approved=False,
                reason=(
                    f"Opening this trade would exceed daily safety threshold: "
                    f"{ctx.daily_loss_pct:.2f}% + {per_trade_risk:.2f}% risk "
                    f"= {projected:.2f}% > {safety_threshold:.2f}% "
                    f"(95% of {budget}% limit)"
                ),
            )

    return RuleResult(approved=True)


def spread_quality_rule(ctx: RuleContext) -> RuleResult:
    si = ctx.symbol_info
    if si is None or si.ask is None or si.bid is None:
        return RuleResult(approved=False, reason="No market data")

    pip = pip_size(si.point, si.digits)
    spread_pips = (si.ask - si.bid) / pip
    sl_pips = abs(ctx.signal.entry_price - ctx.signal.stop_loss) / pip

    if spread_pips > sl_pips * ctx.config.sl_ratio_threshold:
        return RuleResult(
            approved=False,
            reason=f"Spread too high ({spread_pips:.1f} pips vs SL {sl_pips:.1f})",
        )
    return RuleResult(approved=True)


# ── Guard rule ─────────────────────────────────────────────────────────────────


def loss_guard_rule(ctx: RuleContext) -> RuleResult:
    """
    Trade-count circuit breaker — all three guards in one call.

    Delegates to LossTracker which sets paused_until to midnight when
    the daily loss % limit is reached.

    Runs first in ALL_RULES so we skip all other checks (including the
    broker symbol_info call) when already paused.
    """
    if ctx.loss_tracker is None:
        return RuleResult(approved=True)

    paused, reason = ctx.loss_tracker.is_paused()
    if paused:
        return RuleResult(approved=False, reason=f"Loss guard: {reason}")
    return RuleResult(approved=True)


def no_hedging_rule(ctx: RuleContext) -> RuleResult:
    if not ctx.config.no_hedging:
        return RuleResult(approved=True)

    incoming_side = (
        OrderSide.BUY
        if ctx.signal.direction == SignalDirection.LONG
        else OrderSide.SELL
    )
    opposing_side = OrderSide.SELL if incoming_side == OrderSide.BUY else OrderSide.BUY

    conflict = next(
        (
            t
            for t in ctx.open_trades
            if t.symbol == ctx.signal.symbol
            and t.side == opposing_side
            and t.status
            in (TradeStatus.PLANNED, TradeStatus.OPEN, TradeStatus.PARTIALLY_CLOSED)
        ),
        None,
    )
    if conflict:
        return RuleResult(
            approved=False,
            reason=f"NO_HEDGING: {opposing_side.value} trade {conflict.id} already open on {ctx.signal.symbol}",
        )
    return RuleResult(approved=True)


# ── Rule list ──────────────────────────────────────────────────────────────────
# loss_guard_rule runs first — it's memory-only and short-circuits
# everything else when paused.

ALL_RULES: List[RiskRule] = [
    loss_guard_rule,
    no_hedging_rule,
    min_rr_rule,
    max_open_trades_rule,
    max_symbol_exposure_rule,
    duplicate_signal_rule,
    daily_loss_limit_rule,
    spread_quality_rule,
]
