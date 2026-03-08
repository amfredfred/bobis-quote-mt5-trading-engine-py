"""
Individual risk rules.

Each rule is a callable with signature:
    rule(signal, open_trades, config, daily_loss_pct) -> RuleResult

Rules are composable: add new ones to ALL_RULES without touching risk_engine.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List

from interfaces.signal_interface import InboundSignal
from interfaces.trade import Trade, TradeStatus
from config.config import RiskConfig


@dataclass(frozen=True)
class RuleResult:
    approved: bool
    reason: str = ""


RiskRule = Callable[[InboundSignal, List[Trade], RiskConfig, float], RuleResult]


# ── Rules ─────────────────────────────────────────────────────────────────────


def min_rr_rule(
    signal: InboundSignal,
    open_trades: List[Trade],
    config: RiskConfig,
    daily_loss_pct: float,
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
) -> RuleResult:
    active = [
        t
        for t in open_trades
        if t.status in (TradeStatus.OPEN, TradeStatus.PARTIALLY_CLOSED)
    ]
    if len(active) >= config.max_open_trades:
        return RuleResult(
            approved=False,
            reason=f"Max open trades reached ({len(active)}/{config.max_open_trades})",
        )
    return RuleResult(approved=True)


def max_symbol_exposure_rule(
    signal: InboundSignal,
    open_trades: List[Trade],
    config: RiskConfig,
    daily_loss_pct: float,
) -> RuleResult:
    symbol_trades = [
        t
        for t in open_trades
        if t.symbol == signal.symbol
        and t.status in (TradeStatus.OPEN, TradeStatus.PARTIALLY_CLOSED)
    ]

    if len(symbol_trades) >= config.max_exposure_per_symbol:
        return RuleResult(
            approved=False,
            reason=(
                f"Symbol exposure limit for {signal.symbol}: "
                f"{len(symbol_trades)}/{config.max_exposure_per_symbol}"
            ),
        )
    return RuleResult(approved=True)


def duplicate_signal_rule(
    signal: InboundSignal,
    open_trades: List[Trade],
    config: RiskConfig,
    daily_loss_pct: float,
) -> RuleResult:
    duplicate = next((t for t in open_trades if t.signal_id == signal.id), None)
    if duplicate:
        return RuleResult(
            approved=False,
            reason=f"Duplicate signal: trade {duplicate.id} already exists for signal {signal.id}",
        )
    return RuleResult(approved=True)


def daily_loss_limit_rule(
    signal: InboundSignal,
    open_trades: List[Trade],
    config: RiskConfig,
    daily_loss_pct: float,
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
