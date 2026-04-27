"""
MT5 connection manager with symbol caching.

Wraps the MetaTrader5 Python package initialization / shutdown lifecycle.
All other MT5 modules receive this client and call `mt5` through it.
Symbol resolution is cached in memory for performance and thread safety.
Broker UTC offset is derived once on connect() by comparing a live tick timestamp
against true UTC. Use it anywhere broker timestamps need converting.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
import threading

import MetaTrader5 as mt5
from src.config.settings import Mt5Config

logger = logging.getLogger(__name__)

_RECONNECT_DELAYS = [2, 4, 8, 16, 30]  # seconds, capped at last value
_OFFSET_SYMBOLS = ["BTCUSD", "ETHUSD", "BTCUSDT", "ETHUSDT", "EURUSD"]  # always live
_MT5_LOCK = threading.Lock()
_SYMBOL_CACHE: dict[str, str] = {}


class Mt5Client:
    """
    Manages the MT5 terminal connection with symbol caching.
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
        Raises ConnectionError on failure.
        """
        logger.info("Connecting to MT5 terminal")

        with _MT5_LOCK:
            if not mt5.initialize(
                login=self._config.login,
                password=self._config.password,
                server=self._config.server,
                path=self._config.path,
            ):
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

    def resolve_symbol(self, base_symbol: str) -> str | None:
        """
        Find the correct broker symbol for a given base symbol.

        Example:
            BTCUSD -> BTCUSDm, BTCUSD.x, etc
        """
        base_symbol = base_symbol.replace("/", "").replace("_", "").upper()

        # Check cache first
        if base_symbol in _SYMBOL_CACHE:
            return _SYMBOL_CACHE[base_symbol]

        with _MT5_LOCK:
            symbols = mt5.symbols_get()

        if not symbols:
            return None

        matches = []

        for s in symbols:
            name = s.name
            upper_name = name.upper()

            # 1. Exact match
            if upper_name == base_symbol:
                _SYMBOL_CACHE[base_symbol] = name
                return name

            # 2. Prefix or suffix match
            if upper_name.startswith(base_symbol) or upper_name.endswith(base_symbol):
                matches.append(name)

        if matches:
            # Prefer shortest (EURUSDm over EURUSDmicro)
            resolved = sorted(matches, key=len)[0]
            _SYMBOL_CACHE[base_symbol] = resolved
            return resolved

        # No match
        return None

    def _derive_broker_utc_offset(self) -> int:
        tick = None
        for symbol in _OFFSET_SYMBOLS:
            _resolved = self.resolve_symbol(symbol)
            if _resolved is None:
                continue
            with _MT5_LOCK:
                t = mt5.symbol_info_tick(_resolved)
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
            with _MT5_LOCK:
                mt5.shutdown()
            self._connected = False
            logger.info("MT5 disconnected")

    def is_connected(self) -> bool:
        """
        Live check — asks the terminal if it is connected to the broker.
        """
        with _MT5_LOCK:
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

        If the terminal is not connected -> attempt to reconnect.
        """
        if self.is_connected():
            return

        logger.warning("MT5 not connected — attempting reconnect")

        for attempt, delay in enumerate(_RECONNECT_DELAYS, start=1):
            logger.info("MT5 reconnect attempt %d/%d", attempt, len(_RECONNECT_DELAYS))
            try:
                with _MT5_LOCK:
                    mt5.shutdown()
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
        """Direct access to MetaTrader5 module for other modules."""
        return mt5









