"""
Wires event bus subscriptions and starts all services.

Separating bootstrap from container keeps construction (what exists)
separate from runtime wiring (what talks to what).
"""

from __future__ import annotations

import logging

from app.container import AppContainer
from config.config import AppConfig
from core.events import Events
from infrastructure.metrics import metrics
from interfaces.signal_interface import InboundSignal

logger = logging.getLogger(__name__)


def bootstrap(container: AppContainer, config: AppConfig) -> None:
    """
    1. Connect to MT5 and verify.
    2. Hydrate position store from live MT5 positions.
    3. Register event handlers.
    4. Start position manager and signal consumer.
    """
    # ── Initialise storage ────────────────────────────────────────────────
    container.trade_repo.init()

    # ── Connect to MT5 and verify ─────────────────────────────────────────
    container.mt5_client.connect()

    # mt5.initialize() can return True even when credentials are wrong —
    # account_info() is the ground truth.
    try:
        account = container.mt5_positions.get_account_info()
    except Exception as exc:
        raise ConnectionError(
            f"MT5 initialized but account_info() failed — "
            f"check login/password/server in .env. Detail: {exc}"
        ) from exc

    container.event_bus.emit(Events.BROKER_CONNECTED)
    logger.info(
        "MT5 connected and verified",
        extra={
            "login": account.login,
            "server": account.server,
            "currency": account.currency,
            "balance": account.balance,
            "equity": account.equity,
            "leverage": account.leverage,
            "free_margin": account.free_margin,
        },
    )

    # ── Hydrate position store from MT5 ───────────────────────────────────
    # MT5 is the single source of truth for open positions.
    # The first _poll() cycle handles anything closed while the engine was down.
    container.position_manager.hydrate_from_broker()

    # ── Prime daily loss before first signal can arrive ───────────────────
    # Without this, _daily_loss_pct=0.0 until the first poll tick fires,
    # meaning a signal arriving before that tick bypasses the daily loss rule.
    try:
        loss_pct = container.mt5_positions.get_daily_loss_pct(config.execution.magic)
        container.execution_engine.update_daily_loss(loss_pct)
        logger.info("Daily loss primed", extra={"daily_loss_pct": loss_pct})
    except Exception:
        logger.warning("bootstrap: could not prime daily loss — defaulting to 0.0")

    # ── Wire: signal.triggered → queue → execution pipeline ──────────────
    def on_signal_triggered(signal: InboundSignal) -> None:
        adapted = container.strategy_router.route(signal)
        container.signal_queue.put(adapted)

    container.event_bus.on(Events.SIGNAL_TRIGGERED, on_signal_triggered)

    # ── Wire: all events → metrics counter ────────────────────────────────
    def on_any_event(event: str, _payload) -> None:
        metrics.increment(f"events.{event}")

    container.event_bus.on_any(on_any_event)

    # ── Monitoring server — built here so container is fully available ────
    from infrastructure.monitoring_server import MonitoringServer

    container.monitoring_server = MonitoringServer(
        container, config, port=config.monitoring_port
    )

    # ── Start services ────────────────────────────────────────────────────
    container.monitoring_server.start()
    container.signal_queue.start()
    container.position_manager.start()
    container.signal_consumer.start()

    container.event_bus.emit(Events.SYSTEM_STARTED)
    logger.info(
        "Execution Engine started",
        extra={
            "symbols": config.signal.symbols,
            "signal_ws": config.signal.ws_url,
            "risk_pct": config.risk.risk_percent_per_trade,
            "max_trades": config.risk.max_open_trades,
        },
    )


def shutdown(container: AppContainer) -> None:
    logger.info("Shutting down Execution Engine")
    container.event_bus.emit(Events.SYSTEM_STOPPING)
    container.signal_consumer.stop()
    container.signal_queue.stop()
    container.position_manager.stop()
    container.monitoring_server.stop()
    container.mt5_client.disconnect()
    logger.info("Shutdown complete")
