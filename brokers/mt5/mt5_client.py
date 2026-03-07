"""
MT5 connection manager.

Wraps the MetaTrader5 Python package initialisation / shutdown lifecycle.
All other MT5 modules receive this client and call `mt5` through it so
the package import is a single seam that can be mocked in tests.

Auto-reconnect:
    If the terminal is closed and reopened, any call that goes through
    ensure_connected() will attempt to re-initialise transparently.
    The position manager and order manager both call this before every
    broker operation so recovery is automatic.
"""

from __future__ import annotations

import logging
import time

import MetaTrader5 as mt5

from config.config import Mt5Config

logger = logging.getLogger(__name__)

_RECONNECT_DELAYS = [2, 4, 8, 16, 30]  # seconds, capped at last value


class Mt5Client:
    """
    Manages the MT5 terminal connection.

    Usage:
        client = Mt5Client(cfg.mt5)
        client.connect()              # raises on failure
        client.ensure_connected()     # call before every broker op
        ...
        client.disconnect()
    """

    def __init__(self, config: Mt5Config) -> None:
        self._config = config
        self._connected = False

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def connect(self) -> None:
        """
        Initialise the MT5 connection.

        If login/password/server are configured, authenticate immediately.
        Otherwise initialise without credentials (manual login assumed).
        Raises ConnectionError on failure.
        """
        logger.info("Connecting to MT5 terminal")

        if not mt5.initialize():
            error = mt5.last_error()
            raise ConnectionError(f"MT5 initialize() failed: {error}")

        if self._config.login:
            authorised = mt5.login(
                login=self._config.login,
                password=self._config.password,
                server=self._config.server,
            )
            if not authorised:
                error = mt5.last_error()
                mt5.shutdown()
                raise ConnectionError(f"MT5 login() failed: {error}")

        info = mt5.terminal_info()
        logger.info(
            "MT5 connected",
            extra={
                "terminal_build": info.build if info else "unknown",
                "login": self._config.login,
                "server": self._config.server,
            },
        )
        self._connected = True

    def disconnect(self) -> None:
        if self._connected:
            mt5.shutdown()
            self._connected = False
            logger.info("MT5 disconnected")

    def is_connected(self) -> bool:
        """
        Live check — asks the terminal if it is connected to the broker.
        Falls back to False if the terminal process is gone entirely.
        """
        info = mt5.terminal_info()
        if info is None:
            self._connected = False
            return False
        connected = bool(info.connected)
        self._connected = connected
        return connected

    def ensure_connected(self) -> None:
        """
        Call before every broker operation.

        If the terminal is still alive and connected -> no-op (fast path).
        If not -> attempt to reconnect with exponential backoff.
        Raises ConnectionError only if all retries are exhausted.
        """
        if self.is_connected():
            return

        logger.warning("MT5 not connected — attempting reconnect")

        for attempt, delay in enumerate(_RECONNECT_DELAYS, start=1):
            logger.info("MT5 reconnect attempt %d/%d", attempt, len(_RECONNECT_DELAYS))
            try:
                mt5.shutdown()  # clean slate before re-init
                self.connect()
                logger.info("MT5 reconnected successfully")
                return
            except ConnectionError as exc:
                logger.warning("MT5 reconnect attempt %d failed: %s", attempt, exc)
                if attempt < len(_RECONNECT_DELAYS):
                    time.sleep(delay)

        raise ConnectionError(
            "MT5 reconnect failed after all attempts — "
            "is the terminal running and logged in?"
        )

    @property
    def mt5(self):
        """
        Direct access to the MetaTrader5 module.

        Exposed so mt5_orders / mt5_positions can call the API without
        importing the package themselves — keeping mocking straightforward.
        """
        return mt5
