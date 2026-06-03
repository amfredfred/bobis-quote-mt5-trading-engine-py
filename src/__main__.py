"""
Trading Execution Engine — entry point.

Startup sequence:
    1. Load configuration from config.yaml
    2. Set up structured logging
    3. Build DI container
    4. Initialise storage directories
    5. Bootstrap services (MT5, signal consumer, position manager)
    6. Block until SIGINT / SIGTERM → graceful shutdown

Usage:
    python -m src                        # reads config.yaml in cwd
    python -m src config/custom.yaml     # explicit path (first CLI arg)
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
from pathlib import Path

from src.app.bootstrap import bootstrap, shutdown
from src.app.container import build_container
from src.config.settings import AppConfig
from src.infra.logger import setup_logging
from src.utils import time as _time

logger = logging.getLogger("main")


def _acquire_single_instance_lock(storage_path: str):
    lock_dir = Path(storage_path)
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_file = open(lock_dir / "execution-engine.lock", "a+", encoding="utf-8")

    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise RuntimeError(
                f"another execution-engine instance is already running for {lock_dir}"
            ) from exc
    else:
        import fcntl

        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError(
                f"another execution-engine instance is already running for {lock_dir}"
            ) from exc

    lock_file.seek(0)
    lock_file.truncate()
    lock_file.write(f"pid={os.getpid()}\n")
    lock_file.flush()
    return lock_file


def main() -> None:
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    cfg = AppConfig.from_yaml(config_path)

    setup_logging(cfg.log_level, cfg.engine_timezone)
    _time.configure(cfg.engine_timezone)

    logger.info(
        "Execution Engine initialising",
        extra={
            "pid": os.getpid(),
            "python": sys.executable,
            "symbols": cfg.signal.symbols,
            "signal_ws": cfg.signal.ws_url,
            "mt5_login": cfg.mt5.login,
            "mt5_server": cfg.mt5.server,
            "mt5_path": cfg.mt5.path,
            "max_losing_streak": cfg.risk.max_losing_streak,
            "max_open_trades": cfg.risk.max_losing_streak + 1,
            "storage_path": cfg.storage_path,
        },
    )

    try:
        instance_lock = _acquire_single_instance_lock(cfg.storage_path)
    except RuntimeError as exc:
        logger.error("%s", exc)
        sys.exit(1)

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
        instance_lock.close()


if __name__ == "__main__":
    main()








