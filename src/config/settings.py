"""
Typed application configuration built from environment variables.

Import `cfg` for access anywhere; it is constructed once at module load.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List
from src.utils.symbol import normalise_symbol
from src.config.env import env
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class RiskConfig:
    max_losing_streak: int
    max_daily_loss_percent: float
    max_exposure_per_symbol: int
    min_rr_ratio: float
    max_lot_size: float
    min_lot_size: float
    sl_ratio_threshold: float
    no_hedging: bool = True


@dataclass(frozen=True)
class ExecutionConfig:
    # R-multiple at which poll-based TP1 detection fires the partial close.
    # TP1 is always entry ± (tp1_rr_multiple × stop_distance), independent of
    # whatever the signal engine computed.
    tp1_rr_multiple: float
    # Percentage of entry_lots to close when price hits TP1.
    # 0 = no partial close; move_sl_to_be_on_tp1 has no effect when this is 0.
    tp1_percentage: float
    # Move SL to entry after a successful TP1 partial close.
    move_sl_to_be_on_tp1: bool
    slippage: int
    magic: int
    comment: str
    spread_risk_multiplier: float
    order_retry_count: int
    max_entry_slippage_pct_of_stop: float  # e.g. 0.20 = reject if slip > 20% of stop distance
    close_on_slippage_exceed: bool         # true = emergency-close; false = warn and continue
    order_retry_delay_sec: int
    # When False (default): SL/TP levels stay at the signal's analysis-derived prices
    # regardless of fill slippage.  The fill price is recorded for PnL tracking but
    # levels are never moved.
    # When True: all levels shift by the fill-vs-signal difference so that stop
    # distance and R:R are preserved relative to the actual fill price.
    adjust_levels_on_slippage: bool = False


@dataclass(frozen=True)
class Mt5Config:
    login: int
    password: str
    server: str
    path: str


@dataclass(frozen=True)
class SignalConfig:
    ws_url: str
    ws_secret_key: str
    symbols: List[str]


@dataclass(frozen=True)
class AppConfig:
    risk: RiskConfig
    execution: ExecutionConfig
    mt5: Mt5Config
    signal: SignalConfig
    storage_path: str
    log_level: str
    position_poll_interval: float
    engine_timezone: ZoneInfo
    monitoring_port: str


def _build() -> AppConfig:
    return AppConfig(
        risk=RiskConfig(
            max_losing_streak=env.MAX_LOSING_STREAK,
            max_daily_loss_percent=env.MAX_DAILY_LOSS_PERCENT,
            max_exposure_per_symbol=env.MAX_EXPOSURE_PER_SYMBOL,
            min_rr_ratio=env.MIN_RR_RATIO,
            max_lot_size=env.MAX_LOT_SIZE,
            min_lot_size=env.MIN_LOT_SIZE,
            sl_ratio_threshold=env.SL_RATIO_THRESHOLD,
            no_hedging=env.NO_HEDGING,
        ),
        execution=ExecutionConfig(
            tp1_rr_multiple=env.TP1_RR_MULTIPLE,
            tp1_percentage=env.TP1_PERCENTAGE,
            move_sl_to_be_on_tp1=env.MOVE_SL_TO_BE_ON_TP1,
            slippage=env.MT5_SLIPPAGE,
            magic=env.MT5_MAGIC,
            comment=env.MT5_COMMENT,
            spread_risk_multiplier=env.SPREAD_RISK_MULTIPLIER,
            order_retry_count=env.ORDER_RETRY_COUNT,
            max_entry_slippage_pct_of_stop=env.MAX_ENTRY_SLIPPAGE_PCT_OF_STOP,
            close_on_slippage_exceed=env.CLOSE_ON_SLIPPAGE_EXCEED,
            order_retry_delay_sec=env.ORDER_RETRY_DELAY_SEC,
            adjust_levels_on_slippage=env.USE_SLIPPAGE_ADJUSTED_LEVELS,
        ),
        mt5=Mt5Config(
            login=env.MT5_LOGIN,
            password=env.MT5_PASSWORD,
            server=env.MT5_SERVER,
            path=env.MT5_EXEC_PATH,
        ),
        signal=SignalConfig(
            ws_url=env.SIGNAL_ENGINE_WS_URL,
            ws_secret_key=env.SIGNAL_ENGINE_WS_SECRET_KEY,
            symbols=[normalise_symbol(s) for s in env.SIGNAL_ENGINE_SYMBOLS.split(",")],
        ),
        storage_path=env.STORAGE_PATH,
        log_level=env.LOG_LEVEL,
        position_poll_interval=env.POSITION_POLL_INTERVAL_SEC,
        engine_timezone=ZoneInfo(env.ENGINE_TIMEZONE),
        monitoring_port=env.MONITORING_PORT,
    )


cfg: AppConfig = _build()









