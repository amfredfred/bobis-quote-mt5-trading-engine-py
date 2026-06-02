"""
risk/risk_rules.py — individual risk rules.

Each rule is a callable:  rule(ctx: RuleContext) -> RuleResult

RuleContext carries everything a rule needs — no global state reads.
Rules are composable: add to ALL_RULES without touching RiskEngine.

Guard rules:
    loss_guard_rule — delegates to LossTracker which tracks daily loss %:
        When MAX_DAILY_LOSS_PERCENT is reached, trading is paused until midnight.

    If ctx.loss_tracker is None (backward compat / tests), guard rules pass.

Rule ordering in ALL_RULES:
    1. Memory-only rules first  — no broker I/O, fast short-circuit.
    2. Symbol-info rules last   — require a live broker tick; only reached
                                  if all memory checks pass.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional, TYPE_CHECKING

from src.domain.signal_interface import InboundSignal, SignalDirection
from src.domain.trade import Trade, OrderSide, TradeStatus
from src.domain.position import SymbolInfo
from src.config.settings import RiskConfig
from src.utils.price import pip_size

if TYPE_CHECKING:
    from src.risk.loss_tracker import LossTracker

_UNKNOWN_SIGNAL_ID = "unknown"


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


# ── Memory-only rules (no broker I/O) ─────────────────────────────────────────


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
            reason=(
                f"NO_HEDGING: {opposing_side.value} trade {conflict.id} "
                f"already open on {ctx.signal.symbol}"
            ),
        )
    return RuleResult(approved=True)


def max_open_trades_rule(ctx: RuleContext) -> RuleResult:
    # max_open_trades is derived — not a separate config value.
    # With MAX_LOSING_STREAK=N, you can open at most N+1 trades simultaneously.
    # This guarantees: max_exposure = (N+1) × risk_per_trade = daily_budget exactly.
    max_open = ctx.config.max_losing_streak + 1
    if ctx.effective_open >= max_open:
        return RuleResult(
            approved=False,
            reason=f"Max open trades reached ({ctx.effective_open}/{max_open})",
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
            if t.signal_id == ctx.signal.id and t.signal_id != _UNKNOWN_SIGNAL_ID
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

    Two layers of protection:

    Layer 1 — Hard safety stop at 95% of MAX_DAILY_LOSS_PERCENT.
        New trades are refused once the realised loss reaches 95% of the
        configured limit. This leaves a 5% buffer so open positions cannot
        push the account past 100% of the daily limit even if they all hit
        SL simultaneously.

    Layer 2 — Pre-trade budget projection.
        Before opening a trade we check whether this trade's per-trade risk,
        added to what has already been lost today, would exceed the 95%
        safety threshold.

        per_trade_risk_pct = MAX_DAILY_LOSS_PERCENT / (MAX_LOSING_STREAK + 1)

        This is the same formula used by LossTracker.daily_risk_amount() —
        one source of truth for how the budget is divided.

        Example (MAX_DAILY_LOSS_PERCENT=5, MAX_LOSING_STREAK=4):
            per_trade_risk_pct = 5 / 5 = 1%
            safety_threshold   = 4.75%
            daily_loss_pct=3.8% → 3.8 + 1.0 = 4.8 > 4.75 → REJECTED
            daily_loss_pct=3.7% → 3.7 + 1.0 = 4.7 < 4.75 → ALLOWED
    """
    budget = ctx.config.max_daily_loss_percent
    safety_threshold = budget * 0.95

    # Layer 1 — hard stop
    if ctx.daily_loss_pct >= safety_threshold:
        return RuleResult(
            approved=False,
            reason=(
                f"Daily loss safety stop: {ctx.daily_loss_pct:.2f}% >= "
                f"{safety_threshold:.2f}% (95% of {budget}% limit)"
            ),
        )

    # Layer 2 — budget projection using streak-derived per-trade risk
    per_trade_risk_pct = budget / (ctx.config.max_losing_streak + 1)
    projected = ctx.daily_loss_pct + per_trade_risk_pct
    if projected > safety_threshold:
        return RuleResult(
            approved=False,
            reason=(
                f"Opening this trade would exceed daily safety threshold: "
                f"{ctx.daily_loss_pct:.2f}% + {per_trade_risk_pct:.2f}% risk "
                f"= {projected:.2f}% > {safety_threshold:.2f}% "
                f"(95% of {budget}% limit)"
            ),
        )

    return RuleResult(approved=True)


# ── Symbol-info rules (require live broker tick) ───────────────────────────────
#
# Shared validation is intentionally repeated across these two rules.
# Rules are independent units — silent coupling through a shared pre-check
# would make individual rules untestable and the failure path ambiguous.


def _resolve_fill_price(si: SymbolInfo, direction: SignalDirection) -> float:
    """Return the expected market-order fill price for the given direction."""
    return si.ask if direction == SignalDirection.LONG else si.bid


def _validate_symbol_info(si: SymbolInfo | None) -> Optional[RuleResult]:
    """Return a RuleResult if symbol info is invalid, else None."""
    if si is None or si.ask is None or si.bid is None:
        return RuleResult(approved=False, reason="No market data")

    if si.ask <= 0 or si.bid <= 0:
        return RuleResult(
            approved=False,
            reason="Invalid market data: zero or negative prices",
        )

    return None


def min_rr_rule(ctx: RuleContext) -> RuleResult:
    """Check R:R from the actual fill price, not the stale signal entry_price.

    A signal generated at entry_price may arrive at execution with a materially
    different ask/bid. Computing RRR from fill price ensures the check reflects
    the trade you are actually opening.
    """
    si = ctx.symbol_info
    invalid = _validate_symbol_info(si)
    if invalid:
        return invalid

    pip = pip_size(si.point, si.digits)
    if pip <= 0:
        return RuleResult(approved=False, reason="Invalid pip size")

    fill_price = _resolve_fill_price(si, ctx.signal.direction)

    sl_pips = abs(fill_price - ctx.signal.stop_loss) / pip
    tp_pips = abs(fill_price - ctx.signal.tp2) / pip

    if sl_pips == 0:
        return RuleResult(approved=False, reason="SL distance is zero")

    actual_rr = tp_pips / sl_pips
    if actual_rr < ctx.config.min_rr_ratio:
        return RuleResult(
            approved=False,
            reason=(
                f"Actual R:R {actual_rr:.2f} < minimum {ctx.config.min_rr_ratio} "
                f"(signal R:R was {ctx.signal.risk_reward_ratio:.2f})"
            ),
        )
    return RuleResult(approved=True)


def spread_quality_rule(ctx: RuleContext) -> RuleResult:
    si = ctx.symbol_info
    invalid = _validate_symbol_info(si)
    if invalid:
        return invalid

    pip = pip_size(si.point, si.digits)
    if pip <= 0:
        return RuleResult(approved=False, reason="Invalid pip size")

    spread_pips = (si.ask - si.bid) / pip
    if spread_pips < 0:
        return RuleResult(approved=False, reason="Invalid market data: negative spread")

    fill_price = _resolve_fill_price(si, ctx.signal.direction)

    sl_pips = abs(fill_price - ctx.signal.stop_loss) / pip
    if sl_pips == 0:
        return RuleResult(approved=False, reason="SL distance is zero")

    if spread_pips / sl_pips > ctx.config.sl_ratio_threshold:
        return RuleResult(
            approved=False,
            reason=(
                f"Spread/SL ratio too high: {spread_pips/sl_pips:.2f} "
                f"({spread_pips:.1f} pip spread vs {sl_pips:.1f} pip SL)"
            ),
        )

    return RuleResult(approved=True)


# ── Rule list ──────────────────────────────────────────────────────────────────
# Ordered by cost: memory-only rules short-circuit before any broker I/O.

ALL_RULES: List[RiskRule] = [
    loss_guard_rule,  # memory-only: paused state check
    no_hedging_rule,  # memory-only: open trades scan
    max_open_trades_rule,  # memory-only: counter check
    max_symbol_exposure_rule,  # memory-only: counter check
    duplicate_signal_rule,  # memory-only: open trades scan
    daily_loss_limit_rule,  # memory-only: loss budget check
    min_rr_rule,  # broker I/O: live fill price
    spread_quality_rule,  # broker I/O: live spread
]
