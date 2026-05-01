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
    SIGNAL_ENGINE_WS_SECRET_KEY: str = _optional("SIGNAL_ENGINE_WS_SECRET_KEY", "")
    SIGNAL_ENGINE_SYMBOLS: str = _optional("SIGNAL_ENGINE_SYMBOLS", "EUR/USD,GBP/USD")

    # ── MT5 ────────────────────────────────────────────────────────────────
    MT5_LOGIN: int = _optional_int("MT5_LOGIN", 0)
    MT5_PASSWORD: str = _optional("MT5_PASSWORD", "")
    MT5_SERVER: str = _optional("MT5_SERVER", "")
    MT5_MAGIC: int = _optional_int("MT5_MAGIC", 20240101)
    MT5_SLIPPAGE: int = _optional_int("MT5_SLIPPAGE", 10)
    MT5_COMMENT: str = _optional("MT5_COMMENT", "signal-engine")
    MT5_EXEC_PATH: str = _optional("MT5_EXEC_PATH", None)

    # ── Risk limits ────────────────────────────────────────────────────────
    MAX_LOSING_STREAK: int = _optional_int("MAX_LOSING_STREAK", 4)
    MAX_DAILY_LOSS_PERCENT: float = _optional_float("MAX_DAILY_LOSS_PERCENT", 5.0)
    MAX_EXPOSURE_PER_SYMBOL: int = _optional_int("MAX_EXPOSURE_PER_SYMBOL", 2)
    MIN_RR_RATIO: float = _optional_float("MIN_RR_RATIO", 1.5)
    MAX_LOT_SIZE: float = _optional_float("MAX_LOT_SIZE", 10.0)
    MIN_LOT_SIZE: float = _optional_float("MIN_LOT_SIZE", 0.01)
    SL_RATIO_THRESHOLD: float = _optional_float("SL_RATIO_THRESHOLD", 0.33)

    # ── Intraday capital-protection guards (parity with Node pipeline) ─────
    # Equity peak drawdown: pause when equity drops >= N% from the session
    # high-water mark.  0.0 = disabled.
    MAX_EQUITY_DRAWDOWN_PERCENT: float = _optional_float("MAX_EQUITY_DRAWDOWN_PERCENT", 2.0)
    # Rolling window: pause when the peak-to-trough swing within the last
    # ROLLING_WINDOW_SIZE equity samples exceeds ROLLING_DRAWDOWN_PCT %.
    # Both must be > 0 for the guard to be active (0 = disabled).
    ROLLING_WINDOW_SIZE: int = _optional_int("ROLLING_WINDOW_SIZE", 0)
    ROLLING_DRAWDOWN_PCT: float = _optional_float("ROLLING_DRAWDOWN_PCT", 0.0)

    # ─────────────────────────────────────────
    # TIMEZONE
    # ─────────────────────────────────────────
    ENGINE_TIMEZONE: str = _optional("ENGINE_TIMEZONE", "Africa/Lagos")

    # ── Execution ──────────────────────────────────────────────────────────
    # R-multiple at which the poll detects TP1 and triggers the partial close.
    # TP1 = entry ± (TP1_RR_MULTIPLE × stop_distance). Must be ≤ MIN_RR_RATIO.
    TP1_RR_MULTIPLE: float = _optional_float("TP1_RR_MULTIPLE", 1.5)
    # Percentage of entry_lots to close at TP1 (0 = disabled, 50 = close half).
    TP1_PERCENTAGE: float = _optional_float("TP1_PERCENTAGE", 50.0)
    # Move the broker SL to breakeven AFTER the TP1 partial close succeeds.
    # Has no effect when TP1_PERCENTAGE=0 (no partial close means no BE move).
    MOVE_SL_TO_BE_ON_TP1: bool = _optional_bool("MOVE_SL_TO_BE_ON_TP1", True)
    NO_HEDGING: bool = _optional_bool("NO_HEDGING", True)

    # ── Live trading protections ───────────────────────────────────────────
    # [1] Max acceptable slippage AFTER a fill
    #     CLOSE_ON_SLIPPAGE_EXCEED=true  → emergency-close and reject the fill
    #     CLOSE_ON_SLIPPAGE_EXCEED=false → warn only, accept the fill and continue
    MAX_ENTRY_SLIPPAGE_PCT_OF_STOP: float = _optional_float("MAX_ENTRY_SLIPPAGE_PCT_OF_STOP", 0.20)  # 20% of stop distance
    CLOSE_ON_SLIPPAGE_EXCEED: bool = _optional_bool("CLOSE_ON_SLIPPAGE_EXCEED", False)
    # When False (default): SL/TP levels are held at the signal's original analysis
    # prices after a slippage fill.  When True: all levels shift by the fill delta
    # so stop distance and R:R are preserved relative to the actual fill price.
    USE_SLIPPAGE_ADJUSTED_LEVELS: bool = _optional_bool("USE_SLIPPAGE_ADJUSTED_LEVELS", False)

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

    MONITORING_PORT: int = _optional_int("MONITORING_PORT", 8080)


def _validate_env(e: Env) -> None:
    if e.MAX_LOSING_STREAK < 1:
        raise ValueError(
            f"MAX_LOSING_STREAK must be >= 1, got: {e.MAX_LOSING_STREAK}. "
            "Set it to your system's worst recorded consecutive losing streak."
        )


env = Env()
_validate_env(env)









