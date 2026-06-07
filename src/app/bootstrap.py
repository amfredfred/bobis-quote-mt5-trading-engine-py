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
from dataclasses import replace
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
        loss_pct, start_equity, current_equity = container.mt5_positions.get_daily_pnl_info(
            config.execution.magic
        )
        container.execution_engine.update_daily_loss(loss_pct, start_equity, current_equity)
        logger.info(
            "Daily loss primed",
            extra={"daily_loss_pct": loss_pct, "start_of_day_equity": start_equity, "current_equity": current_equity},
        )
    except Exception as e:
        logger.warning("Daily loss priming failed — using safe defaults: %s", e)
        # Force a minimal update with current equity as start
        account = container.mt5_positions.get_account_info()
        container.execution_engine.update_daily_loss(0.0, account.equity, account.equity)

    # Wire: signal.triggered → queue → execution pipeline
    def on_signal_triggered(signal: InboundSignal) -> None:
        resolved = container.mt5_positions.resolve_symbol(signal.symbol)
        if not resolved:
            logger.error(
                "Signal rejected: symbol not found in MT5",
                extra={"signal_id": signal.id, "symbol": signal.symbol},
            )
            container.event_bus.emit(
                Events.SIGNAL_REJECTED,
                {"signal": signal, "reason": "symbol_not_found"},
            )
            return
        enriched = replace(signal, resolved_symbol=resolved)
        adapted = container.strategy_router.route(enriched)
        container.signal_queue.put(adapted)

    container.event_bus.on(Events.SIGNAL_TRIGGERED, on_signal_triggered)

    # Wire: all events → metrics counter
    def on_any_event(event: str, _payload) -> None:
        metrics.increment(f"events.{event}")

    container.event_bus.on_any(on_any_event)

    # Report customer-visible execution lifecycle transitions off the trading path.
    container.event_bus.on_any(container.signal_consumer.report_event)

    # UI bridge — WebSocket server for the dashboard
    from src.infra.ui_bridge import UIBridge
    container.ui_bridge = UIBridge(container, config, port=config.monitoring_port)
    container.signal_consumer.set_snapshot_provider(
        container.ui_bridge.build_remote_snapshot
    )

    # Wire remote command callbacks — called by the gateway's command.pause /
    # command.resume / command.emergency_stop events via SignalConsumer.
    # Each callback raises ValueError with a machine-readable reason code when
    # the command is invalid for the current engine state.  The caller
    # (SignalConsumer._handle_command) catches the exception and sends
    # command.failed back to the gateway.

    def _on_pause() -> None:
        """Reject if already paused; otherwise pause the signal queue."""
        if container.signal_queue.is_paused():
            raise ValueError("ENGINE_ALREADY_PAUSED")
        container.signal_queue.pause()

    def _on_resume() -> None:
        """Reject if not paused; otherwise resume the signal queue."""
        if not container.signal_queue.is_paused():
            raise ValueError("ENGINE_NOT_PAUSED")
        container.signal_queue.resume()

    def _on_emergency_stop() -> None:
        """Reject if no open positions; otherwise pause and close all."""
        open_trades = container.position_store.get_open_trades()
        if not open_trades:
            raise ValueError("NO_OPEN_POSITIONS")
        container.signal_queue.pause()
        container.position_manager.emergency_close_all()

    container.signal_consumer.set_command_callbacks(
        on_pause=_on_pause,
        on_resume=_on_resume,
        on_emergency_stop=_on_emergency_stop,
    )

    # Start all services
    container.ui_bridge.start()
    container.signal_queue.start()
    container.position_manager.start()
    container.signal_consumer.start()

    container.event_bus.emit(Events.SYSTEM_STARTED)
    logger.info(
        "Execution Engine started",
        extra={
            "symbols":          config.gateway.symbols,
            "gateway_ws":       config.gateway.ws_url,
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
    if container.ui_bridge:
        container.ui_bridge.stop()
    container.mt5_client.disconnect()
    metrics.stop()
    logger.info("Shutdown complete")
