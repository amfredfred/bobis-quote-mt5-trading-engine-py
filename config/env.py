"""
Environment variable loader.

All config that varies between deployments lives here.
Raises on missing required variables so the engine fails fast at startup.
"""

from __future__ import annotations

import os
from dotenv import load_dotenv
load_dotenv()

def _optional(key: str, fallback: str) -> str:
    return os.environ.get(key, fallback)


def _optional_int(key: str, fallback: int) -> int:
    raw = os.environ.get(key)
    if raw is None:
        return fallback
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{key} must be an integer, got: {raw!r}")


def _optional_float(key: str, fallback: float) -> float:
    raw = os.environ.get(key)
    if raw is None:
        return fallback
    try:
        return float(raw)
    except ValueError:
        raise ValueError(f"{key} must be a float, got: {raw!r}")


def _optional_bool(key: str, fallback: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return fallback
    return raw.lower() in ("true", "1", "yes")


class Env:
    """Parsed environment variables. Instantiate once at startup."""

    # ── Signal Engine ──────────────────────────────────────────────────────
    SIGNAL_ENGINE_WS_URL: str = _optional("SIGNAL_ENGINE_WS_URL", "ws://localhost:8765")
    SIGNAL_ENGINE_SYMBOLS: str = _optional("SIGNAL_ENGINE_SYMBOLS", "EUR/USD,GBP/USD")

    # ── MT5 ────────────────────────────────────────────────────────────────
    MT5_LOGIN: int = _optional_int("MT5_LOGIN", 0)
    MT5_PASSWORD: str = _optional("MT5_PASSWORD", "")
    MT5_SERVER: str = _optional("MT5_SERVER", "")
    MT5_MAGIC: int = _optional_int("MT5_MAGIC", 20240101)
    MT5_SLIPPAGE: int = _optional_int("MT5_SLIPPAGE", 10)
    MT5_COMMENT: str = _optional("MT5_COMMENT", "signal-engine")
    MT5_EXEC_PATH: str = _optional("MT5_EXEC_PATH", None)

    # ── Risk mode ──────────────────────────────────────────────────────────
    RISK_MODE: str = _optional("RISK_MODE", "percentage")
    RISK_PERCENT_PER_TRADE: float = _optional_float("RISK_PERCENT_PER_TRADE", 1.0)
    RISK_FIXED_AMOUNT: float = _optional_float("RISK_FIXED_AMOUNT", 100.0)

    # ── Risk limits ────────────────────────────────────────────────────────
    MAX_OPEN_TRADES: int = _optional_int("MAX_OPEN_TRADES", 5)
    MAX_DAILY_LOSS_PERCENT: float = _optional_float("MAX_DAILY_LOSS_PERCENT", 5.0)
    MAX_EXPOSURE_PER_SYMBOL: int = _optional_int("MAX_EXPOSURE_PER_SYMBOL", 2)
    MIN_RR_RATIO: float = _optional_float("MIN_RR_RATIO", 1.5)
    MAX_LOT_SIZE: float = _optional_float("MAX_LOT_SIZE", 10.0)
    MIN_LOT_SIZE: float = _optional_float("MIN_LOT_SIZE", 0.01)

    # ── Execution ──────────────────────────────────────────────────────────
    TP1_PARTIAL_CLOSE_PERCENT: float = _optional_float(
        "TP1_PARTIAL_CLOSE_PERCENT", 50.0
    )
    MOVE_SL_TO_BE_ON_TP1: bool = _optional_bool("MOVE_SL_TO_BE_ON_TP1", True)

    # ── Live trading protections ───────────────────────────────────────────
    # [1] Max acceptable slippage AFTER a fill — warns and cancels if exceeded
    MAX_ENTRY_SLIPPAGE_PIPS: float = _optional_float("MAX_ENTRY_SLIPPAGE_PIPS", 3.0)

    # [2] Spread surcharge — adds N × spread to the SL distance before sizing
    #     0.0 = disabled (demo behaviour)  |  1.0 = add full spread to risk
    SPREAD_RISK_MULTIPLIER: float = _optional_float("SPREAD_RISK_MULTIPLIER", 1.0)

    # [3] Order retry on requote/rejection
    ORDER_RETRY_COUNT: int = _optional_int("ORDER_RETRY_COUNT", 2)
    ORDER_RETRY_DELAY_SEC: float = _optional_float("ORDER_RETRY_DELAY_SEC", 0.5)

    # ── Position Manager ───────────────────────────────────────────────────
    POSITION_POLL_INTERVAL_SEC: float = _optional_float(
        "POSITION_POLL_INTERVAL_SEC", 5.0
    )

    # ── Infrastructure ─────────────────────────────────────────────────────
    LOG_LEVEL: str = _optional("LOG_LEVEL", "INFO")
    STORAGE_PATH: str = _optional("STORAGE_PATH", "./data")


env = Env()
