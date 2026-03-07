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
from interfaces.trade import Trade

logger = logging.getLogger(__name__)


def bootstrap(container: AppContainer, config: AppConfig) -> None:
    """
    1. Hydrate open trades from disk.
    2. Connect to MT5.
    3. Register event handlers.
    4. Start position manager and signal consumer.
    """
    # ── Hydrate open trades ───────────────────────────────────────────────
    open_trades = container.trade_repo.load_open_trades()
    if open_trades:
        container.position_store.hydrate(open_trades)
        logger.info("Hydrated open trades from disk", extra={"count": len(open_trades)})

    # ── Connect to MT5 and verify ─────────────────────────────────────────
    container.mt5_client.connect()

    # Confirm the connection is real by fetching live account info.
    # mt5.initialize() can return True even when credentials are wrong or
    # the terminal is not fully ready — account_info() is the ground truth.
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

    # ── Wire: signal.triggered → execution pipeline ───────────────────────
    def on_signal_triggered(signal: InboundSignal) -> None:
        adapted = container.strategy_router.route(signal)
        container.execution_engine.execute(adapted)

    container.event_bus.on(Events.SIGNAL_TRIGGERED, on_signal_triggered)

    # ── Wire: trade.closed → update daily stats ───────────────────────────
    def on_trade_closed(trade: Trade) -> None:
        if trade.realized_pnl is None:
            return
        try:
            account_info = container.mt5_client.mt5.account_info()
            balance = account_info.balance if account_info else 0.0
            stats = container.account_repo.record_trade_closed(
                pnl=trade.realized_pnl,
                win=(trade.realized_pnl or 0) > 0,
                start_balance=balance,
            )
            loss_pct = container.account_repo.daily_loss_percent(stats)
            container.execution_engine.update_daily_loss(loss_pct)
            metrics.set_gauge("account.daily_loss_pct", loss_pct)
        except Exception:
            logger.exception("bootstrap: failed to update daily stats")

    container.event_bus.on(Events.TRADE_CLOSED, on_trade_closed)

    # ── Wire: all events → metrics counter ───────────────────────────────
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
