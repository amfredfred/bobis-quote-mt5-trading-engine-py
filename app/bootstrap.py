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

    # ── Wire: signal.triggered → execution pipeline ───────────────────────
    def on_signal_triggered(signal: InboundSignal) -> None:
        adapted = container.strategy_router.route(signal)
        container.execution_engine.execute(adapted)

    container.event_bus.on(Events.SIGNAL_TRIGGERED, on_signal_triggered)

    # ── Wire: all events → metrics counter ────────────────────────────────
    def on_any_event(event: str, _payload) -> None:
        metrics.increment(f"events.{event}")

    container.event_bus.on_any(on_any_event)

    # ── Start services ────────────────────────────────────────────────────
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
    container.position_manager.stop()
    container.mt5_client.disconnect()
    logger.info("Shutdown complete")
