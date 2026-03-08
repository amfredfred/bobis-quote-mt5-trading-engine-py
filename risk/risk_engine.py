"""
Runs all configured risk rules against a signal.
Short-circuits on the first failure.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional

from config.config import RiskConfig
from infrastructure.metrics import metrics
from .risk_rules import ALL_RULES, RiskRule
from interfaces.signal_interface import InboundSignal
from interfaces.trade import Trade

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
    ) -> None:
        self._config = config
        self._rules = rules if rules is not None else ALL_RULES

    def evaluate(
        self,
        signal: InboundSignal,
        open_trades: List[Trade],
        daily_loss_pct: float,
    ) -> RiskDecision:

        for rule in self._rules:
            result = rule(signal, open_trades, self._config, daily_loss_pct)
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
