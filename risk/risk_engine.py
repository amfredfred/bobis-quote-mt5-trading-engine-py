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
from risk.risk_rules import ALL_RULES, RiskRule
from interfaces.signal_interface import InboundSignal
from interfaces.position import SymbolInfo
from interfaces.trade import Trade
from typing import Optional, Sequence

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
        open_trades: Sequence[Trade],
        daily_loss_pct: float,
        effective_open: int = 0,
        effective_symbol: int = 0,
        symbol_info: Optional[SymbolInfo] = None,
    ) -> RiskDecision:

        if not self._rules:
            raise ValueError("No risk rules configured")

        for rule in self._rules:
            result = rule(
                signal,
                open_trades,
                self._config,
                daily_loss_pct,
                effective_open,
                effective_symbol,
                symbol_info,
            )

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
                "direction": getattr(signal.direction, "value", signal.direction),
                "rr": signal.risk_reward_ratio,
            },
        )
        metrics.increment("risk.approved")

        return RiskDecision(approved=True)
