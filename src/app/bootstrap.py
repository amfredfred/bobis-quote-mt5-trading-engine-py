"""
app/bootstrap.py — wires event bus subscriptions and starts all services.

Import change: interfaces.signal_interface → interfaces
All wiring logic is unchanged.
"""

from __future__ import annotations

import logging

from src.app.container import AppContainer
from src.config.settings import AppConfig
from src.core.event_types import Events
from src.infra.metrics import metrics
from src.domain.signal_interface import InboundSignal

logger = logging.getLogger(__name__)


def bootstrap(container: AppContainer, config: AppConfig) -> None:
    """
    1. Init database schema.
    2. Connect to MT5 and verify.
    3. Hydrate position store from live MT5 positions.
    4. Register event handlers.
    5. Start all services.
    """
    # Init database
    container.db.init()
    container.trade_repo.init()

    # Restore metrics from last session
    metrics.init_db(container.db)

    # Connect to MT5
    container.mt5_client.connect()

    try:
        account = container.mt5_positions.get_account_info()
    except Exception as exc:
        raise ConnectionError(
            f"MT5 initialised but account_info() failed — "
            f"check login/password/server in .env.  Detail: {exc}"
        ) from exc

    container.event_bus.emit(Events.BROKER_CONNECTED)
    logger.info(
        "MT5 connected",
        extra={
            "login":       account.login,
            "server":      account.server,
            "currency":    account.currency,
            "balance":     account.balance,
            "equity":      account.equity,
            "leverage":    account.leverage,
            "free_margin": account.free_margin,
        },
    )

    # Hydrate position store
    container.position_manager.hydrate_from_broker()

    # Prime daily loss + start-of-day equity before first signal
    try:
        loss_pct, start_equity = container.mt5_positions.get_daily_pnl_info(config.execution.magic)
        container.execution_engine.update_daily_loss(loss_pct, start_equity)
        logger.info(
            "Daily loss primed",
            extra={"daily_loss_pct": loss_pct, "start_of_day_equity": start_equity},
        )
    except Exception:
        logger.warning("bootstrap: could not prime daily loss — defaulting to 0.0")

    # Wire: signal.triggered → queue → execution pipeline
    def on_signal_triggered(signal: InboundSignal) -> None:
        adapted = container.strategy_router.route(signal)
        container.signal_queue.put(adapted)

    container.event_bus.on(Events.SIGNAL_TRIGGERED, on_signal_triggered)

    # Wire: all events → metrics counter
    def on_any_event(event: str, _payload) -> None:
        metrics.increment(f"events.{event}")

    container.event_bus.on_any(on_any_event)

    # Monitoring server
    from src.infra.monitoring import MonitoringServer
    container.monitoring_server = MonitoringServer(container, config, port=config.monitoring_port)

    # Start all services
    container.monitoring_server.start()
    container.signal_queue.start()
    container.position_manager.start()
    container.signal_consumer.start()

    container.event_bus.emit(Events.SYSTEM_STARTED)
    logger.info(
        "Execution Engine started",
        extra={
            "symbols":          config.signal.symbols,
            "signal_ws":        config.signal.ws_url,
            "max_losing_streak": config.risk.max_losing_streak,
            "max_open_trades":  config.risk.max_losing_streak + 1,
        },
    )


def shutdown(container: AppContainer) -> None:
    logger.info("Shutting down Execution Engine")
    container.event_bus.emit(Events.SYSTEM_STOPPING)
    container.signal_consumer.stop()
    container.signal_queue.stop()
    container.position_manager.stop()
    if container.monitoring_server:
        container.monitoring_server.stop()
    container.mt5_client.disconnect()
    metrics.stop()
    logger.info("Shutdown complete")









