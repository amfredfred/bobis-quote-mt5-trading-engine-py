"""
System-wide event name constants.

Using string constants (not enums) keeps event names plain and serialisable.
"""

from __future__ import annotations


class Events:
    # ── Signal ────────────────────────────────────────────────────────────
    SIGNAL_RECEIVED  = "signal.received"
    SIGNAL_VALIDATED = "signal.validated"
    SIGNAL_REJECTED  = "signal.rejected"
    SIGNAL_TRIGGERED = "signal.triggered"

    # ── Risk ──────────────────────────────────────────────────────────────
    RISK_APPROVED = "risk.approved"
    RISK_REJECTED = "risk.rejected"

    # ── Trade lifecycle ───────────────────────────────────────────────────
    TRADE_PLANNED    = "trade.planned"
    TRADE_OPENED     = "trade.opened"
    TRADE_TP1_HIT    = "trade.tp1_hit"
    TRADE_TP2_HIT    = "trade.tp2_hit"
    TRADE_SL_HIT     = "trade.sl_hit"
    TRADE_INVALIDATED= "trade.invalidated"
    TRADE_EXPIRED    = "trade.expired"
    TRADE_CLOSED     = "trade.closed"
    TRADE_ERROR      = "trade.error"

    # ── Order ─────────────────────────────────────────────────────────────
    ORDER_CREATED  = "order.created"
    ORDER_EXECUTED = "order.executed"
    ORDER_REJECTED = "order.rejected"

    # ── Broker ────────────────────────────────────────────────────────────
    BROKER_CONNECTED    = "broker.connected"
    BROKER_DISCONNECTED = "broker.disconnected"
    BROKER_ERROR        = "broker.error"

    # ── System ────────────────────────────────────────────────────────────
    SYSTEM_STARTED  = "system.started"
    SYSTEM_STOPPING = "system.stopping"
    DAILY_RESET     = "system.daily_reset"
