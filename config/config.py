"""
Typed application configuration built from environment variables.

Import `cfg` for access anywhere; it is constructed once at module load.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from config.env import env
from utils.lot_calculator import RiskMode


def _parse_risk_mode(raw: str) -> RiskMode:
    try:
        return RiskMode(raw.lower().strip())
    except ValueError:
        raise ValueError(f"RISK_MODE must be 'percentage' or 'fixed', got: {raw!r}")


@dataclass(frozen=True)
class RiskConfig:
    risk_mode: RiskMode
    risk_percent_per_trade: float  # used when mode=PERCENTAGE
    risk_fixed_amount: float  # used when mode=FIXED
    max_open_trades: int
    max_daily_loss_percent: float
    max_exposure_per_symbol: int
    min_rr_ratio: float
    max_lot_size: float
    min_lot_size: float


@dataclass(frozen=True)
class ExecutionConfig:
    tp1_partial_close_percent: float
    move_sl_to_be_on_tp1: bool
    slippage: int
    magic: int
    comment: str
    spread_risk_multiplier: float
    order_retry_count: int
    max_entry_slippage_pips: int


@dataclass(frozen=True)
class Mt5Config:
    login: int
    password: str
    server: str
    path: str


@dataclass(frozen=True)
class SignalConfig:
    ws_url: str
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


def _build() -> AppConfig:
    return AppConfig(
        risk=RiskConfig(
            risk_mode=_parse_risk_mode(env.RISK_MODE),
            risk_percent_per_trade=env.RISK_PERCENT_PER_TRADE,
            risk_fixed_amount=env.RISK_FIXED_AMOUNT,
            max_open_trades=env.MAX_OPEN_TRADES,
            max_daily_loss_percent=env.MAX_DAILY_LOSS_PERCENT,
            max_exposure_per_symbol=env.MAX_EXPOSURE_PER_SYMBOL,
            min_rr_ratio=env.MIN_RR_RATIO,
            max_lot_size=env.MAX_LOT_SIZE,
            min_lot_size=env.MIN_LOT_SIZE,
        ),
        execution=ExecutionConfig(
            tp1_partial_close_percent=env.TP1_PARTIAL_CLOSE_PERCENT,
            move_sl_to_be_on_tp1=env.MOVE_SL_TO_BE_ON_TP1,
            slippage=env.MT5_SLIPPAGE,
            magic=env.MT5_MAGIC,
            comment=env.MT5_COMMENT,
            spread_risk_multiplier=env.SPREAD_RISK_MULTIPLIER,
            order_retry_count=env.ORDER_RETRY_COUNT,
            max_entry_slippage_pips=env.MAX_ENTRY_SLIPPAGE_PIPS,
        ),
        mt5=Mt5Config(
            login=env.MT5_LOGIN,
            password=env.MT5_PASSWORD,
            server=env.MT5_SERVER,
            path=env.MT5_EXEC_PATH,
        ),
        signal=SignalConfig(
            ws_url=env.SIGNAL_ENGINE_WS_URL,
            symbols=[s.strip() for s in env.SIGNAL_ENGINE_SYMBOLS.split(",")],
        ),
        storage_path=env.STORAGE_PATH,
        log_level=env.LOG_LEVEL,
        position_poll_interval=env.POSITION_POLL_INTERVAL_SEC,
    )


cfg: AppConfig = _build()
