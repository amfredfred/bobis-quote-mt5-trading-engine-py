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
    sl_ratio_threshold: float
    no_hedging: bool = True

    # ── Trade-count circuit-breaker guards ─────────────────────────────────
    # All four timing/count values below are derived from max_consecutive_losses
    # in _build() — only MAX_CONSECUTIVE_LOSSES lives in the .env.
    #
    # Derivation (N = MAX_CONSECUTIVE_LOSSES):
    #   pause_after_streak_h  = N × 1.2   e.g. N=10 → 12.0 h
    #   max_daily_losses      = N          e.g. N=10 → 10 per day
    #   max_losses_per_window = N          e.g. N=10 → 10 in rolling window
    #   loss_window_hours     = N          e.g. N=10 → 10 h rolling window
    max_consecutive_losses: int = 3
    pause_after_streak_h: float = 3.6    # derived: N × 1.2
    max_daily_losses: int = 3            # derived: N
    max_losses_per_window: int = 3       # derived: N
    loss_window_hours: float = 3.0       # derived: float(N)


@dataclass(frozen=True)
class ExecutionConfig:
    tp1_partial_close_percent: float
    # R-multiple at which the partial close fires.  Replaces signal.tp1 so the
    # level is always entry ± (tp1_rr_multiple × stop_distance), independent of
    # whatever the signal engine computed.  After partial + BE the rest runs to
    # signal.tp2 risk-free.
    tp1_rr_multiple: float
    move_sl_to_be_on_tp1: bool
    slippage: int
    magic: int
    comment: str
    spread_risk_multiplier: float
    order_retry_count: int
    max_entry_slippage_pct_of_stop: float  # e.g. 0.20 = reject if slip > 20% of stop distance
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


def _derive_streak_params(n: int) -> tuple[float, int, int, float]:
    """Derive all guard timing/count values from a single consecutive-loss knob.

    Returns (pause_after_streak_h, max_daily_losses, max_losses_per_window, loss_window_hours).

    Rationale:
      pause_after_streak_h  = N × 1.2  — cool-off scales with streak severity
      max_daily_losses      = N        — daily cap equals the streak threshold
      max_losses_per_window = N        — rolling window uses the same count
      loss_window_hours     = N        — window length (hours) equals the count

    Example  MAX_CONSECUTIVE_LOSSES=10:
      pause=12.0 h  |  max_daily=10  |  window_count=10  |  window_hours=10.0
    """
    return (
        round(n * 1.2, 1),  # pause_after_streak_h
        n,                  # max_daily_losses
        n,                  # max_losses_per_window
        float(n),           # loss_window_hours
    )


def _build() -> AppConfig:
    n = env.MAX_CONSECUTIVE_LOSSES
    pause_h, max_daily, max_window_count, window_h = _derive_streak_params(n)

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
            sl_ratio_threshold=env.SL_RATIO_THRESHOLD,
            max_consecutive_losses=n,
            pause_after_streak_h=pause_h,
            max_daily_losses=max_daily,
            max_losses_per_window=max_window_count,
            loss_window_hours=window_h,
            no_hedging=env.NO_HEDGING,
        ),
        execution=ExecutionConfig(
            tp1_partial_close_percent=env.TP1_PARTIAL_CLOSE_PERCENT,
            tp1_rr_multiple=env.TP1_RR_MULTIPLE,
            move_sl_to_be_on_tp1=env.MOVE_SL_TO_BE_ON_TP1,
            slippage=env.MT5_SLIPPAGE,
            magic=env.MT5_MAGIC,
            comment=env.MT5_COMMENT,
            spread_risk_multiplier=env.SPREAD_RISK_MULTIPLIER,
            order_retry_count=env.ORDER_RETRY_COUNT,
            max_entry_slippage_pct_of_stop=env.MAX_ENTRY_SLIPPAGE_PCT_OF_STOP,
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
