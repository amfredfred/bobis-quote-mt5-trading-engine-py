"""
risk/risk_rules.py — individual risk rules.

Each rule is a callable:  rule(ctx: RuleContext) -> RuleResult

RuleContext carries everything a rule needs — no global state reads.
Rules are composable: add to ALL_RULES without touching RiskEngine.

Guard rules (new):
    loss_guard_rule — delegates to LossTracker which covers all three guards:
        Guard 1: consecutive streak  (MAX_CONSECUTIVE_LOSSES / PAUSE_AFTER_STREAK_H)
        Guard 2: daily cap           (MAX_DAILY_LOSSES)
        Guard 3: rolling window      (MAX_LOSSES_PER_WINDOW / LOSS_WINDOW_HOURS)

    If ctx.loss_tracker is None (backward compat / tests), guard rules pass.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence, TYPE_CHECKING

from interfaces.signal_interface import InboundSignal
from interfaces.trade import Trade
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
    """Monetary daily drawdown guard — sourced from MT5 account equity."""
    if ctx.daily_loss_pct >= ctx.config.max_daily_loss_percent:
        return RuleResult(
            approved=False,
            reason=(
                f"Daily loss limit reached: "
                f"{ctx.daily_loss_pct:.2f}% >= {ctx.config.max_daily_loss_percent}%"
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

    Delegates to LossTracker which maintains a combined paused_until
    timestamp across streak, daily-cap, and rolling-window guards.

    Runs first in ALL_RULES so we skip all other checks (including the
    broker symbol_info call) when already paused.
    """
    if ctx.loss_tracker is None:
        return RuleResult(approved=True)

    paused, reason = ctx.loss_tracker.is_paused()
    if paused:
        return RuleResult(approved=False, reason=f"Loss guard: {reason}")
    return RuleResult(approved=True)


# ── Rule list ──────────────────────────────────────────────────────────────────
# loss_guard_rule runs first — it's memory-only and short-circuits
# everything else when paused.

ALL_RULES: List[RiskRule] = [
    loss_guard_rule,
    min_rr_rule,
    max_open_trades_rule,
    max_symbol_exposure_rule,
    duplicate_signal_rule,
    daily_loss_limit_rule,
    spread_quality_rule,
]
