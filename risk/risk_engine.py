"""
risk/risk_engine.py — evaluates a signal against all configured risk rules.

Change: accepts optional loss_tracker and passes it into RuleContext so
guard rules have access to trade-count circuit-breaker state.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional, Sequence, TYPE_CHECKING

from interfaces.signal_interface import InboundSignal
from interfaces.trade import Trade
from interfaces.position import SymbolInfo
from config.config import RiskConfig
from infrastructure.metrics import metrics
from risk.risk_rules import ALL_RULES, RuleContext, RuleResult, RiskRule

if TYPE_CHECKING:
    from risk.loss_tracker import LossTracker

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    reason: Optional[str] = None


class RiskEngine:

    def __init__(
        self,
        config: RiskConfig,
        rules: Optional[List[RiskRule]] = None,
        loss_tracker: Optional["LossTracker"] = None,
    ) -> None:
        self._config = config
        self._rules = rules if rules is not None else ALL_RULES
        self._loss_tracker = loss_tracker

    def set_loss_tracker(self, tracker: "LossTracker") -> None:
        """Wire in LossTracker after construction (container convenience)."""
        self._loss_tracker = tracker

    def evaluate(
        self,
        signal: InboundSignal,
        open_trades: Sequence[Trade],
        daily_loss_pct: float,
        effective_open: int = 0,
        effective_symbol: int = 0,
        symbol_info: Optional[SymbolInfo] = None,
    ) -> RiskDecision:

        if not self._rules:
            raise ValueError("No risk rules configured")

        ctx = RuleContext(
            signal=signal,
            open_trades=list(open_trades),
            config=self._config,
            daily_loss_pct=daily_loss_pct,
            effective_open=effective_open,
            effective_symbol=effective_symbol,
            symbol_info=symbol_info,  # type: ignore[arg-type]
            loss_tracker=self._loss_tracker,
        )

        for rule in self._rules:
            result: RuleResult = rule(ctx)
            if not result.approved:
                logger.warning(
                    "Risk rejected",
                    extra={
                        "signal_id": signal.id,
                        "symbol": signal.symbol,
                        "reason": result.reason,
                    },
                )
                metrics.increment("risk.rejected")
                return RiskDecision(approved=False, reason=result.reason)

        logger.info(
            "Risk approved",
            extra={
                "signal_id": signal.id,
                "symbol": signal.symbol,
                "direction": signal.direction.value,
                "rr": signal.risk_reward_ratio,
            },
        )
        metrics.increment("risk.approved")
        return RiskDecision(approved=True)
