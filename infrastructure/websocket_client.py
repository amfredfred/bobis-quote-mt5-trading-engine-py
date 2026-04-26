"""
Resilient WebSocket client with exponential-backoff reconnection.

Runs in a dedicated daemon thread.  Call `start()` once; the client
reconnects automatically on drop.  Pass `on_message` to receive frames.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

import websocket  # websocket-client

logger = logging.getLogger(__name__)


class WebSocketClient:
    def __init__(
        self,
        url: str,
        secret_key: str,
        on_message: Callable[[str], None],
        on_connected: Optional[Callable[[], None]] = None,
        on_disconnected: Optional[Callable[[], None]] = None,
        reconnect_delay: float = 2.0,
        max_reconnect_delay: float = 30.0,
        ping_interval: float = 20.0,
    ) -> None:
        self._url               = url
        self._on_message        = on_message
        self._on_connected      = on_connected or (lambda: None)
        self._on_disconnected   = on_disconnected or (lambda: None)
        self._reconnect_delay   = reconnect_delay
        self._max_reconnect     = max_reconnect_delay
        self._ping_interval     = ping_interval
        self._secret_key        = secret_key

        self._ws:      Optional[websocket.WebSocketApp] = None
        self._stopped  = threading.Event()
        self._thread:  Optional[threading.Thread] = None
        self._current_delay = reconnect_delay

    # ── Public API ────────────────────────────────────────────────────────

    def start(self) -> None:
        logger.info("WebSocketClient starting, url=%s", self._url)
        self._stopped.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        logger.info("WebSocketClient stopping")
        self._stopped.set()
        if self._ws:
            self._ws.close()

    def send(self, data: str) -> bool:
        if self._ws:
            try:
                self._ws.send(data)
                return True
            except Exception:
                logger.warning("WebSocketClient.send failed")
        return False

    # ── Internal loop ─────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        while not self._stopped.is_set():
            self._ws = websocket.WebSocketApp(
                self._url,
                header={"sec-websocket-protocol": f"{self._secret_key}"},
                on_open=self._handle_open,
                on_message=self._handle_message,
                on_error=self._handle_error,
                on_close=self._handle_close,
            )
            self._ws.run_forever(ping_interval=int(self._ping_interval))

            if self._stopped.is_set():
                break

            logger.info(
                "WebSocketClient reconnecting in %.1fs", self._current_delay
            )
            time.sleep(self._current_delay)
            self._current_delay = min(
                self._current_delay * 2, self._max_reconnect
            )

    def _handle_open(self, ws: websocket.WebSocketApp) -> None:
        logger.info("WebSocketClient connected: %s", self._url)
        self._current_delay = self._reconnect_delay   # reset backoff
        self._on_connected()

    def _handle_message(self, ws: websocket.WebSocketApp, message: str) -> None:
        try:
            self._on_message(message)
        except Exception:
            logger.exception("WebSocketClient message handler error")

    def _handle_error(self, ws: websocket.WebSocketApp, error: Exception) -> None:
        logger.error("WebSocketClient error: %s", error)

    def _handle_close(
        self,
        ws: websocket.WebSocketApp,
        close_status_code: Optional[int],
        close_msg: Optional[str],
    ) -> None:
        logger.warning(
            "WebSocketClient closed: code=%s msg=%s", close_status_code, close_msg
        )
        self._on_disconnected()
