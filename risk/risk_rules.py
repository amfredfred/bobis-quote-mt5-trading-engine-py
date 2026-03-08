"""
Individual risk rules.

Each rule is a callable with signature:
    rule(signal, open_trades, config, daily_loss_pct, effective_open, effective_symbol) -> RuleResult

effective_open   = len(open_trades) + pending orders not yet in store (global)
effective_symbol = open + pending for the signal's specific symbol

These prevent the race condition where rapid signals bypass limits before
any order has filled and been added to the store.

Rules are composable: add new ones to ALL_RULES without touching risk_engine.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List

from config.config import RiskConfig
from interfaces.signal_interface import InboundSignal
from interfaces.trade import Trade, TradeStatus


@dataclass(frozen=True)
class RuleResult:
    approved: bool
    reason: str = ""


RiskRule = Callable[
    [InboundSignal, List[Trade], RiskConfig, float, int, int], RuleResult
]


# ── Rules ─────────────────────────────────────────────────────────────────────


def min_rr_rule(
    signal: InboundSignal,
    open_trades: List[Trade],
    config: RiskConfig,
    daily_loss_pct: float,
    effective_open: int,
    effective_symbol: int,
) -> RuleResult:
    if signal.risk_reward_ratio < config.min_rr_ratio:
        return RuleResult(
            approved=False,
            reason=f"R:R {signal.risk_reward_ratio:.2f} < minimum {config.min_rr_ratio}",
        )
    return RuleResult(approved=True)


def max_open_trades_rule(
    signal: InboundSignal,
    open_trades: List[Trade],
    config: RiskConfig,
    daily_loss_pct: float,
    effective_open: int,
    effective_symbol: int,
) -> RuleResult:
    if effective_open >= config.max_open_trades:
        return RuleResult(
            approved=False,
            reason=f"Max open trades reached ({effective_open}/{config.max_open_trades})",
        )
    return RuleResult(approved=True)


def max_symbol_exposure_rule(
    signal: InboundSignal,
    open_trades: List[Trade],
    config: RiskConfig,
    daily_loss_pct: float,
    effective_open: int,
    effective_symbol: int,
) -> RuleResult:
    if effective_symbol >= config.max_exposure_per_symbol:
        return RuleResult(
            approved=False,
            reason=(
                f"Symbol exposure limit for {signal.symbol}: "
                f"{effective_symbol}/{config.max_exposure_per_symbol}"
            ),
        )
    return RuleResult(approved=True)


def duplicate_signal_rule(
    signal: InboundSignal,
    open_trades: List[Trade],
    config: RiskConfig,
    daily_loss_pct: float,
    effective_open: int,
    effective_symbol: int,
) -> RuleResult:
    # Stubs have signal_id="unknown" — exclude them from duplicate detection
    duplicate = next(
        (
            t
            for t in open_trades
            if t.signal_id == signal.id and t.signal_id != "unknown"
        ),
        None,
    )
    if duplicate:
        return RuleResult(
            approved=False,
            reason=f"Duplicate signal: trade {duplicate.id} already open for signal {signal.id}",
        )
    return RuleResult(approved=True)


def daily_loss_limit_rule(
    signal: InboundSignal,
    open_trades: List[Trade],
    config: RiskConfig,
    daily_loss_pct: float,
    effective_open: int,
    effective_symbol: int,
) -> RuleResult:
    if daily_loss_pct >= config.max_daily_loss_percent:
        return RuleResult(
            approved=False,
            reason=(
                f"Daily loss limit reached: "
                f"{daily_loss_pct:.2f}% >= {config.max_daily_loss_percent}%"
            ),
        )
    return RuleResult(approved=True)


ALL_RULES: List[RiskRule] = [
    min_rr_rule,
    max_open_trades_rule,
    max_symbol_exposure_rule,
    duplicate_signal_rule,
    daily_loss_limit_rule,
]
