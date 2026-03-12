"""
MT5 connection manager.

Wraps the MetaTrader5 Python package initialisation / shutdown lifecycle.
All other MT5 modules receive this client and call `mt5` through it so
the package import is a single seam that can be mocked in tests.

broker_utc_offset_hours is derived once on connect() by comparing a live
tick timestamp against true UTC. Use it anywhere broker timestamps need
converting to UTC — no pytz, no global state, no repeated queries.

Auto-reconnect:
    If the terminal is closed and reopened, any call that goes through
    ensure_connected() will attempt to re-initialise transparently.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import MetaTrader5 as mt5

from config.config import Mt5Config

logger = logging.getLogger(__name__)

_RECONNECT_DELAYS = [2, 4, 8, 16, 30]  # seconds, capped at last value
_OFFSET_SYMBOLS = ["BTCUSD", "ETHUSD", "BTCUSDT", "ETHUSDT", "EURUSD"]  # always live

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
        self.broker_utc_offset_hours: int = 0  # derived once on connect()

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
                path=self._config.path,
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
                "path": self._config.path,
            },
        )
        self._connected = True
        self.broker_utc_offset_hours = self._derive_broker_utc_offset()

    def _derive_broker_utc_offset(self) -> int:
        tick = None
        for symbol in _OFFSET_SYMBOLS:
            t = mt5.symbol_info_tick(symbol)
            if t is not None:
                tick = t
                break

        if tick is None:
            logger.warning("Mt5Client: no crypto tick available — assuming UTC+0")
            return 0

        true_utc_now = datetime.now(timezone.utc).timestamp()
        broker_ts = tick.time_msc / 1000.0 if tick.time_msc else float(tick.time)

        raw_offset = (broker_ts - true_utc_now) / 3600
        offset = round(raw_offset)

        logger.info(
            "Mt5Client: broker UTC offset derived from %s",
            symbol,
            extra={"offset_hours": offset, "raw_offset_hours": raw_offset},
        )
        return offset
    
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
