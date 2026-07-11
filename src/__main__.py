"""
Apex Quantel Execution Engine — entry point.

Usage:
    python -m src                      # config.yaml auto-located
    python -m src path/to/config.yaml  # explicit config path

This is a headless service — run directly, or via Task Scheduler (Windows,
see install.ps1)/systemd (Linux) as a background service. It connects
straight to the signal engine's own WebSocket server (no gateway, no GUI,
no remote control).
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


def main() -> None:
    # Explicit config path from CLI args takes priority. If none is supplied,
    # fall back to the same lookup order the packaged build has always used.
    explicit_cfg = next(
        (a for a in sys.argv[1:] if not a.startswith("-")), None
    )
    if explicit_cfg:
        config_path: str = explicit_cfg
    else:
        from src.config.paths import locate_config_path
        config_path = str(locate_config_path())

    cfg = AppConfig.from_yaml(config_path)
    setup_logging(cfg.log_level, cfg.engine_timezone)
    _time.configure(cfg.engine_timezone)

    # File logging: always adjacent to whichever config.yaml was loaded, so
    # everything (config, logs, storage) lives together in one folder.
    from src.infra.logger import add_file_handler
    logs_dir = Path(config_path).resolve().parent / "logs"
    try:
        add_file_handler(logs_dir, cfg.engine_timezone)
    except Exception as exc:
        # Non-fatal: stdout logging still works (systemd captures it via
        # journal; Task Scheduler does not capture it anywhere, so file
        # logging failing there means these lines are effectively lost).
        logger.warning("Could not enable file logging to %s: %s", logs_dir, exc)

    logger.info(
        "Execution Engine initialising",
        extra={
            "pid":               os.getpid(),
            "python":            sys.executable,
            "symbols":           cfg.signal_engine.symbols,
            "signal_engine_ws":  cfg.signal_engine.ws_url,
            "mt5_login":         cfg.mt5.login,
            "mt5_server":        cfg.mt5.server,
            "mt5_path":          cfg.mt5.path,
            "max_losing_streak": cfg.risk.max_losing_streak,
            "storage_path":      cfg.storage_path,
        },
    )

    # Single-instance lock
    lock_dir = Path(cfg.storage_path)
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_file = open(lock_dir / "execution-engine.lock", "a+", encoding="utf-8")

    if os.name == "nt":
        import msvcrt
        try:
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            logger.error("Another instance is already running: %s", exc)
            sys.exit(1)
    else:
        import fcntl
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            logger.error("Another instance is already running: %s", exc)
            sys.exit(1)

    lock_file.seek(0)
    lock_file.truncate()
    lock_file.write(f"pid={os.getpid()}\n")
    lock_file.flush()

    container = build_container(cfg)
    container.trade_repo.init()

    try:
        bootstrap(container, cfg)
    except Exception:
        logger.exception("Fatal error during bootstrap")
        sys.exit(1)

    stop_event = threading.Event()

    def _handle_signal(signum: int, frame) -> None:
        logger.info("Shutdown signal received: %s", signal.Signals(signum).name)
        stop_event.set()

    signal.signal(signal.SIGINT,  _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    logger.info("Execution Engine running — waiting for shutdown signal")

    try:
        while not stop_event.is_set():
            stop_event.wait(timeout=1.0)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        shutdown(container)
        lock_file.close()


if __name__ == "__main__":
    main()
