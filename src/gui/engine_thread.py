"""
src/gui/engine_thread.py — restartable engine background thread.

Manages the full engine lifecycle (bootstrap → run → shutdown) inside a
daemon thread so the GUI main thread stays responsive.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable

logger = logging.getLogger(__name__)


class EngineThread:
    """
    Wraps the trading engine so it can be started, stopped, and restarted
    without restarting the whole process.

    Status values: stopped | starting | running | stopping | error
    """

    def __init__(self, config_path: str = "config.yaml") -> None:
        self.config_path = config_path
        self.status: str = "stopped"
        self.error_msg: str | None = None

        # Called on the engine thread — GUI should use app.after() to update UI.
        self.on_status_change: Callable[[str, str | None], None] | None = None

        self._thread: threading.Thread | None = None
        self._stop_event: threading.Event | None = None
        self._lock = threading.Lock()

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                logger.warning("EngineThread.start() called while already running — ignored")
                return
            self._stop_event = threading.Event()
            self._thread = threading.Thread(
                target=self._run, name="engine", daemon=True
            )
            self._set_status("starting")
            self._thread.start()

    def stop(self, timeout: float = 15.0) -> None:
        self._set_status("stopping")
        stop_ev = None
        with self._lock:
            stop_ev = self._stop_event
        if stop_ev:
            stop_ev.set()
        if self._thread:
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                logger.warning("EngineThread: did not stop within %.0fs", timeout)
        self._set_status("stopped")

    def restart(self) -> None:
        logger.info("EngineThread restart")
        self.stop()
        self.start()

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # ── Internal ──────────────────────────────────────────────────────────────

    def _set_status(self, status: str, error: str | None = None) -> None:
        self.status = status
        self.error_msg = error
        if self.on_status_change:
            try:
                self.on_status_change(status, error)
            except Exception:
                pass

    def _run(self) -> None:
        try:
            from src.app.bootstrap import bootstrap, shutdown
            from src.app.container import build_container
            from src.config.settings import AppConfig
            from src.infra.logger import setup_logging
            from src.utils import time as _time

            cfg = AppConfig.from_yaml(self.config_path)

            # setup_logging clears handlers — re-attach GUI handler afterwards.
            setup_logging(cfg.log_level, cfg.engine_timezone)
            from src.gui.log_relay import get_handler
            logging.getLogger().addHandler(get_handler())

            _time.configure(cfg.engine_timezone)

            container = build_container(cfg)
            container.trade_repo.init()

            bootstrap(container, cfg)
            self._set_status("running")
            logger.info("Engine running in GUI mode")

            assert self._stop_event is not None
            while not self._stop_event.is_set():
                self._stop_event.wait(timeout=1.0)

            self._set_status("stopping")
            shutdown(container)
            self._set_status("stopped")

        except Exception as exc:
            logger.exception("Engine thread fatal error")
            self._set_status("error", str(exc)[:120])
