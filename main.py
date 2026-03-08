"""
Trading Execution Engine — entry point.

Startup sequence:
    1. Load configuration from environment
    2. Set up structured logging
    3. Build DI container
    4. Initialise storage directories
    5. Bootstrap services (MT5, signal consumer, position manager)
    6. Block until SIGINT / SIGTERM → graceful shutdown

Usage:
    python main.py

    or with env vars:
    SIGNAL_ENGINE_WS_URL=ws://localhost:8765 \\
    MT5_LOGIN=12345 MT5_PASSWORD=secret MT5_SERVER=Broker-Live \\ 
    RISK_PERCENT_PER_TRADE=1.0 \\
    python main.py
"""

from __future__ import annotations

import logging
import signal
import sys
import threading

from app.bootstrap import bootstrap, shutdown
from app.container import build_container
from config.config import cfg
from infrastructure.logger import setup_logging

logger = logging.getLogger("main")


def main() -> None:
    setup_logging(cfg.log_level)

    logger.info(
        "Execution Engine initialising",
        extra={
            "symbols": cfg.signal.symbols,
            "signal_ws": cfg.signal.ws_url,
            "risk_pct": cfg.risk.risk_percent_per_trade,
            "max_trades": cfg.risk.max_open_trades,
            "storage_path": cfg.storage_path,
        },
    )

    container = build_container(cfg)

    # ── Initialise storage ────────────────────────────────────────────────
    container.trade_repo.init()

    # ── Bootstrap ─────────────────────────────────────────────────────────
    try:
        bootstrap(container, cfg)
    except Exception:
        logger.exception("Fatal error during bootstrap")
        sys.exit(1)

    # ── Graceful shutdown ─────────────────────────────────────────────────
    # threading.Event works on all platforms (Windows + Unix).
    # signal.pause() is Unix-only and unavailable on Windows.
    stop_event = threading.Event()

    def _handle_signal(signum: int, frame) -> None:
        logger.info("Shutdown signal received: %s", signal.Signals(signum).name)
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    logger.info("Execution Engine running — waiting for signals")

    # Block the main thread with a timeout loop so KeyboardInterrupt
    # (Ctrl-C on Windows) is still delivered between wakeups.
    try:
        while not stop_event.is_set():
            stop_event.wait(timeout=1.0)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        shutdown(container)


if __name__ == "__main__":
    main()
