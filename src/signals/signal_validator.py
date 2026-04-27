"""
Structural and business-logic validation for inbound signals.

Does NOT perform risk checks — that is the Risk Engine's responsibility.
Returns a ValidationResult so callers can inspect errors without catching.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List

from src.domain.signal_interface import InboundSignal

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    valid: bool
    errors: List[str] = field(default_factory=list)


class SignalValidator:
    def validate(self, signal: InboundSignal) -> ValidationResult:
        errors: List[str] = []

        # ── Direction ──────────────────────────────────────────────────────
        if signal.direction.value not in ("LONG", "SHORT"):
            errors.append(f"Unknown direction: {signal.direction}")

        # ── Price sanity ───────────────────────────────────────────────────
        for name, val in [
            ("entryPrice", signal.entry_price),
            ("stopLoss", signal.stop_loss),
            ("tp1", signal.tp1),
            ("tp2", signal.tp2),
        ]:
            if val <= 0:
                errors.append(f"{name} must be > 0")

        if signal.direction.value == "LONG":
            if signal.stop_loss >= signal.entry_price:
                errors.append("LONG: stopLoss must be below entryPrice")
            if signal.tp1 <= signal.entry_price:
                errors.append("LONG: tp1 must be above entryPrice")
            if signal.tp2 <= signal.tp1:
                errors.append("LONG: tp2 must be above tp1")

        if signal.direction.value == "SHORT":
            if signal.stop_loss <= signal.entry_price:
                errors.append("SHORT: stopLoss must be above entryPrice")
            if signal.tp1 >= signal.entry_price:
                errors.append("SHORT: tp1 must be below entryPrice")
            if signal.tp2 >= signal.tp1:
                errors.append("SHORT: tp2 must be below tp1")

        # ── R:R ───────────────────────────────────────────────────────────
        if signal.risk_reward_ratio <= 0:
            errors.append("riskRewardRatio must be > 0")
        if signal.risk_pips <= 0:
            errors.append("riskPips must be > 0")

        # ── HTF range ─────────────────────────────────────────────────────
        htf = signal.htf_range
        if htf.range_high <= htf.range_low:
            errors.append("htfRange: rangeHigh must be > rangeLow")
        if htf.tp_level == 0:
            errors.append("htfRange: tpLevel must be set")
        if htf.bos_direction.value not in ("BULLISH", "BEARISH"):
            errors.append(f"htfRange: unknown bosDirection: {htf.bos_direction}")

        # ── LTF range ─────────────────────────────────────────────────────
        ltf = signal.ltf_range
        if ltf.range_high <= ltf.range_low:
            errors.append("ltfRange: rangeHigh must be > rangeLow")

        # ── Timestamps ────────────────────────────────────────────────────
        if not signal.created_at or signal.created_at <= 0:
            errors.append("createdAt must be a valid timestamp")

        if errors:
            logger.warning(
                "Signal validation failed",
                extra={"signal_id": signal.id, "errors": errors},
            )

        return ValidationResult(valid=len(errors) == 0, errors=errors)









