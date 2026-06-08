"""
Apex Quant Trader — agent entry point.

Modes:
    GUI (default)      python -m src
    Headless service   python -m src --headless
    Custom config      python -m src path/to/config.yaml
    Headless + config  python -m src --headless config/custom.yaml

GUI mode opens the desktop control panel.  The panel connects to the
running apex-quant-trader-agent Windows service and lets you start/stop
it, edit config, and monitor live trades.  Closing the GUI does NOT stop
the engine service.

Headless mode IS the service — run by NSSM directly.  The engine connects
to the cloud gateway so the online dashboard works 24/7.
"""

from __future__ import annotations

import sys


def _is_headless() -> bool:
    return "--headless" in sys.argv


def _headless_main() -> None:
    """
    Original service-mode entry point — blocks until SIGINT / SIGTERM.
    """
    import logging
    import os
    import signal
    import threading
    from pathlib import Path

    from src.app.bootstrap import bootstrap, shutdown
    from src.app.container import build_container
    from src.config.settings import AppConfig
    from src.infra.logger import setup_logging
    from src.utils import time as _time

    logger = logging.getLogger("main")

    # First positional arg that isn't a flag
    config_path = next(
        (a for a in sys.argv[1:] if not a.startswith("-")), "config.yaml"
    )

    cfg = AppConfig.from_yaml(config_path)
    setup_logging(cfg.log_level, cfg.engine_timezone)
    _time.configure(cfg.engine_timezone)

    logger.info(
        "Execution Engine initialising (headless)",
        extra={
            "pid":               os.getpid(),
            "python":            sys.executable,
            "symbols":           cfg.gateway.symbols,
            "gateway_ws":        cfg.gateway.ws_url,
            "engine_id":         cfg.gateway.engine_id,
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


def _gui_main() -> None:
    """
    Launch the CustomTkinter desktop app — single instance only.

    On Windows a named mutex is created before the window opens.  If another
    GUI process already holds that mutex we focus its window and exit silently.
    """
    if sys.platform == "win32":
        import ctypes

        _MUTEX_NAME = "Global\\ApexQuantTrader_GUI_v1"
        _ERROR_ALREADY_EXISTS = 183

        kernel32 = ctypes.windll.kernel32
        _mutex = kernel32.CreateMutexW(None, True, _MUTEX_NAME)
        if kernel32.GetLastError() == _ERROR_ALREADY_EXISTS:
            # Another instance is running — bring its window to the foreground.
            user32 = ctypes.windll.user32
            hwnd = user32.FindWindowW(None, "Apex Quant Trader")
            if hwnd:
                # Restore if minimised, then force to front.
                SW_RESTORE = 9
                user32.ShowWindow(hwnd, SW_RESTORE)
                user32.SetForegroundWindow(hwnd)
            # Release our handle and exit without showing a window.
            kernel32.CloseHandle(_mutex)
            sys.exit(0)
        # Keep _mutex referenced so Python doesn't GC it before mainloop exits.
        # It is released automatically when the process ends.
        _gui_main._mutex = _mutex  # type: ignore[attr-defined]

    from src.gui.app import ApexTraderGUI, resolve_config_path
    app = ApexTraderGUI(config_path=resolve_config_path(sys.argv))
    app.mainloop()


def main() -> None:
    if _is_headless():
        _headless_main()
    else:
        _gui_main()


if __name__ == "__main__":
    main()
