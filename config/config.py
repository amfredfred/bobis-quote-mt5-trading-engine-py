"""
Typed application configuration built from environment variables.

Import `cfg` for access anywhere; it is constructed once at module load.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List
from utils.symbol_utils import normalise_symbol
from config.env import env
from utils.lot_calculator import RiskMode
from zoneinfo import ZoneInfo


def _parse_risk_mode(raw: str) -> RiskMode:
    try:
        return RiskMode(raw.lower().strip())
    except ValueError:
        raise ValueError(f"RISK_MODE must be 'percentage' or 'fixed', got: {raw!r}")


@dataclass(frozen=True)
class RiskConfig:
    risk_mode: RiskMode
    risk_percent_per_trade: float
    risk_fixed_amount: float
    max_open_trades: int
    max_daily_loss_percent: float
    max_exposure_per_symbol: int
    min_rr_ratio: float
    max_lot_size: float
    min_lot_size: float

    # ── Trade-count circuit-breaker guards ─────────────────────────────────
    # Guard 1 — consecutive streak
    max_consecutive_losses: int = 3
    pause_after_streak_h: float = 12.0

    # Guard 2 — daily cap: stop after N losing trades on one calendar day
    # 0 = disabled
    max_daily_losses: int = 3

    # Guard 3 — rolling window: cooldown after N losses within W hours
    # 0 = disabled
    max_losses_per_window: int = 2
    loss_window_hours: float = 4.0


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
    order_retry_delay_sec: int


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
    engine_timezone: ZoneInfo
    monitoring_port: str


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
            max_consecutive_losses=env.MAX_CONSECUTIVE_LOSSES,
            pause_after_streak_h=env.PAUSE_AFTER_STREAK_H,
            max_daily_losses=env.MAX_DAILY_LOSSES,
            max_losses_per_window=env.MAX_LOSSES_PER_WINDOW,
            loss_window_hours=env.LOSS_WINDOW_HOURS,
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
            order_retry_delay_sec=env.ORDER_RETRY_DELAY_SEC,
        ),
        mt5=Mt5Config(
            login=env.MT5_LOGIN,
            password=env.MT5_PASSWORD,
            server=env.MT5_SERVER,
            path=env.MT5_EXEC_PATH,
        ),
        signal=SignalConfig(
            ws_url=env.SIGNAL_ENGINE_WS_URL,
            symbols=[normalise_symbol(s) for s in env.SIGNAL_ENGINE_SYMBOLS.split(",")],
        ),
        storage_path=env.STORAGE_PATH,
        log_level=env.LOG_LEVEL,
        position_poll_interval=env.POSITION_POLL_INTERVAL_SEC,
        engine_timezone=ZoneInfo(env.ENGINE_TIMEZONE),
        monitoring_port=env.MONITORING_PORT,
    )


cfg: AppConfig = _build()
