"""
Application configuration — loaded from config.yaml at startup.

Usage:
    cfg = AppConfig.from_yaml()                    # looks for config.yaml in cwd
    cfg = AppConfig.from_yaml("path/to/config.yaml")
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import yaml
from dotenv import load_dotenv

from src.utils.symbol import normalise_symbol


def _validate_pct_range(name: str, value: float) -> None:
    if value <= 0.0 or value >= 100.0:
        raise ValueError(f"{name} must be > 0 and < 100.")


def _validate_pct_inclusive(name: str, value: float) -> None:
    if value < 0.0 or value > 100.0:
        raise ValueError(f"{name} must be between 0 and 100.")


def _parse_tf_overrides(raw: Any) -> Dict[str, Dict[str, Any]]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("execution.tf_overrides must be a mapping.")

    parsed: Dict[str, Dict[str, Any]] = {}
    for pair, values in raw.items():
        if not isinstance(values, dict):
            raise ValueError(f"execution.tf_overrides.{pair} must be a mapping.")
        override: Dict[str, Any] = {}
        if "tp1_trigger_pct" in values:
            override["tp1_trigger_pct"] = float(values["tp1_trigger_pct"])
        if "tp1_percentage" in values:
            override["tp1_percentage"] = float(values["tp1_percentage"])
        if "tp1_close_pct" in values:
            override["tp1_percentage"] = float(values["tp1_close_pct"])
        parsed[str(pair)] = override
    return parsed


def _tf_pair_key(htf_interval: str | None, ltf_interval: str | None) -> str | None:
    if not htf_interval or not ltf_interval:
        return None
    return f"{_interval_to_minutes(htf_interval)}/{_interval_to_minutes(ltf_interval)}"


def _interval_to_minutes(interval: str) -> int:
    value = interval.strip().lower()
    units = (
        ("minutes", 1),
        ("minute", 1),
        ("mins", 1),
        ("min", 1),
        ("m", 1),
        ("hours", 60),
        ("hour", 60),
        ("hrs", 60),
        ("hr", 60),
        ("h", 60),
        ("days", 1440),
        ("day", 1440),
        ("d", 1440),
    )
    for suffix, multiplier in units:
        if value.endswith(suffix):
            return int(value[: -len(suffix)]) * multiplier
    return int(value)


@dataclass(frozen=True)
class RiskConfig:
    max_losing_streak: int
    max_daily_loss_percent: float
    max_exposure_per_symbol: int
    min_rr_ratio: float
    max_lot_size: float
    min_lot_size: float
    sl_ratio_threshold: float
    symbol_sl_ratio_threshold: Dict[str, float]
    no_hedging: bool = True
    max_equity_drawdown_percent: float = 2.0
    rolling_window_size: int = 0
    rolling_drawdown_pct: float = 0.0

    def __post_init__(self) -> None:
        if self.max_losing_streak < 1:
            raise ValueError(
                f"risk.max_losing_streak must be >= 1, got: {self.max_losing_streak}"
            )


@dataclass(frozen=True)
class ExecutionConfig:
    tp1_trigger_pct: float   # 0–100: TP1 fires at this % of the entry→TP2 range
    tp1_percentage: float
    move_sl_to_be_on_tp1: bool
    slippage: int
    magic: int
    comment: str
    spread_risk_multiplier: float
    order_retry_count: int
    max_entry_slippage_pct_of_stop: float
    close_on_slippage_exceed: bool
    order_retry_delay_sec: float
    breakeven_spread_multiplier: float = 1.5
    breakeven_max_buffer_pct_of_risk: float = 10.0
    adjust_levels_on_slippage: bool = False
    max_signal_age_ms: int = 90_000
    tf_overrides: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_pct_range("execution.tp1_trigger_pct", self.tp1_trigger_pct)
        _validate_pct_inclusive("execution.tp1_percentage", self.tp1_percentage)
        if (
            self.breakeven_spread_multiplier < 0.0
            or 0.0 < self.breakeven_spread_multiplier < 1.0
        ):
            raise ValueError(
                "execution.breakeven_spread_multiplier must be 0 or >= 1."
            )
        _validate_pct_inclusive(
            "execution.breakeven_max_buffer_pct_of_risk",
            self.breakeven_max_buffer_pct_of_risk,
        )
        for key, override in self.tf_overrides.items():
            if "tp1_trigger_pct" in override:
                _validate_pct_range(
                    f"execution.tf_overrides.{key}.tp1_trigger_pct",
                    float(override["tp1_trigger_pct"]),
                )
            if "tp1_percentage" in override:
                _validate_pct_inclusive(
                    f"execution.tf_overrides.{key}.tp1_percentage",
                    float(override["tp1_percentage"]),
                )
            if "tp1_close_pct" in override:
                _validate_pct_inclusive(
                    f"execution.tf_overrides.{key}.tp1_close_pct",
                    float(override["tp1_close_pct"]),
                )

    def tp1_trigger_pct_for(
        self, htf_interval: str | None, ltf_interval: str | None
    ) -> float:
        key = _tf_pair_key(htf_interval, ltf_interval)
        if key is None:
            return self.tp1_trigger_pct
        override = self.tf_overrides.get(key, {})
        return float(override.get("tp1_trigger_pct", self.tp1_trigger_pct))

    def tp1_percentage_for(
        self, htf_interval: str | None, ltf_interval: str | None
    ) -> float:
        key = _tf_pair_key(htf_interval, ltf_interval)
        if key is None:
            return self.tp1_percentage
        override = self.tf_overrides.get(key, {})
        return float(
            override.get(
                "tp1_percentage",
                override.get("tp1_close_pct", self.tp1_percentage),
            )
        )


@dataclass(frozen=True)
class Mt5Config:
    login: int
    password: str
    server: str
    path: str


@dataclass(frozen=True)
class GatewayConfig:
    ws_url: str
    activation_key: str
    engine_id: str
    engine_version: str
    symbols: list[str]
    room_ttl_seconds: int
    signal_hmac_secret: Optional[str] = None

    def __post_init__(self) -> None:
        if len(self.engine_id) < 8:
            raise ValueError("gateway.engine_id must be at least 8 characters.")
        if len(self.activation_key) < 16:
            raise ValueError("APEX_ACTIVATION_KEY must be at least 16 characters.")
        if self.room_ttl_seconds < 30 or self.room_ttl_seconds > 86400:
            raise ValueError("gateway.room_ttl_seconds must be between 30 and 86400.")
        if not self.symbols:
            raise ValueError("gateway.symbols must contain at least one symbol.")


@dataclass(frozen=True)
class AppConfig:
    risk: RiskConfig
    execution: ExecutionConfig
    mt5: Mt5Config
    gateway: GatewayConfig
    storage_path: str
    log_level: str
    position_poll_interval: float
    engine_timezone: ZoneInfo
    monitoring_port: int

    @classmethod
    def from_yaml(cls, path: Path | str = "config.yaml") -> "AppConfig":
        # Load .env if present — kept for backward compatibility with existing
        # installations that still have a .env file.  New installs write
        # everything into config.yaml and no longer need .env.
        load_dotenv(override=False)

        with open(path, "r", encoding="utf-8") as fh:
            raw: dict = yaml.safe_load(fh)

        gateway = raw.get("gateway", {})
        mt5 = raw.get("mt5", {})
        risk = raw.get("risk", {})
        exe = raw.get("execution", {})
        eng = raw.get("engine", {})

        gateway_symbols_raw = gateway.get("symbols", [])
        if isinstance(gateway_symbols_raw, str):
            gateway_symbols_raw = [s.strip() for s in gateway_symbols_raw.split(",")]

        # Secrets: prefer config.yaml values; fall back to env vars so that
        # existing .env-based installs continue to work without reconfiguration.
        mt5_password = str(mt5.get("password") or os.environ.get("MT5_PASSWORD", ""))
        activation_key = str(
            gateway.get("activation_key") or os.environ.get("APEX_ACTIVATION_KEY", "")
        )
        signal_hmac_secret = (
            gateway.get("signal_hmac_secret") or os.environ.get("SIGNAL_HMAC_SECRET") or None
        )

        return cls(
            gateway=GatewayConfig(
                ws_url=str(gateway["ws_url"]),
                activation_key=activation_key,
                engine_id=str(gateway.get("engine_id", f"execution-{mt5['login']}")),
                engine_version=str(gateway.get("engine_version", "0.1.0")),
                symbols=[
                    normalise_symbol(str(symbol)) for symbol in gateway_symbols_raw
                ],
                room_ttl_seconds=int(gateway.get("room_ttl_seconds", 3600)),
                signal_hmac_secret=signal_hmac_secret,
            ),
            mt5=Mt5Config(
                login=int(mt5["login"]),
                password=mt5_password,
                server=str(mt5["server"]),
                path=str(mt5.get("path", "")),
            ),
            risk=RiskConfig(
                max_losing_streak=int(risk["max_losing_streak"]),
                max_daily_loss_percent=float(risk["max_daily_loss_percent"]),
                max_exposure_per_symbol=int(risk["max_exposure_per_symbol"]),
                min_rr_ratio=float(risk["min_rr_ratio"]),
                max_lot_size=float(risk["max_lot_size"]),
                min_lot_size=float(risk.get("min_lot_size", 0.01)),
                sl_ratio_threshold=float(risk["sl_ratio_threshold"]),
                symbol_sl_ratio_threshold={
                    normalise_symbol(str(symbol)): float(threshold)
                    for symbol, threshold in risk.get(
                        "symbol_sl_ratio_threshold", {}
                    ).items()
                },
                no_hedging=bool(risk.get("no_hedging", True)),
                max_equity_drawdown_percent=float(risk.get("max_equity_drawdown_percent", 2.0)),
                rolling_window_size=int(risk.get("rolling_window_size", 0)),
                rolling_drawdown_pct=float(risk.get("rolling_drawdown_pct", 0.0)),
            ),
            execution=ExecutionConfig(
                tp1_trigger_pct=float(exe["tp1_trigger_pct"]),
                tp1_percentage=float(exe["tp1_percentage"]),
                move_sl_to_be_on_tp1=bool(exe.get("move_sl_to_be_on_tp1", True)),
                slippage=int(mt5.get("slippage", 10)),
                magic=int(mt5.get("magic", 20240101)),
                comment=str(mt5.get("comment", "signal-engine")),
                spread_risk_multiplier=float(exe.get("spread_risk_multiplier", 1.0)),
                order_retry_count=int(exe.get("order_retry_count", 2)),
                max_entry_slippage_pct_of_stop=float(exe.get("max_entry_slippage_pct_of_stop", 0.20)),
                close_on_slippage_exceed=bool(exe.get("close_on_slippage_exceed", False)),
                order_retry_delay_sec=float(exe.get("order_retry_delay_sec", 0.5)),
                breakeven_spread_multiplier=float(
                    exe.get("breakeven_spread_multiplier", 1.5)
                ),
                breakeven_max_buffer_pct_of_risk=float(
                    exe.get("breakeven_max_buffer_pct_of_risk", 10.0)
                ),
                adjust_levels_on_slippage=bool(exe.get("adjust_levels_on_slippage", False)),
                max_signal_age_ms=int(exe.get("max_signal_age_ms", 90_000)),
                tf_overrides=_parse_tf_overrides(exe.get("tf_overrides")),
            ),
            storage_path=str(eng.get("storage_path", "./data")),
            log_level=str(eng.get("log_level", "INFO")),
            position_poll_interval=float(eng.get("position_poll_interval", 5.0)),
            engine_timezone=ZoneInfo(str(eng.get("timezone", "UTC"))),
            monitoring_port=int(eng.get("monitoring_port", 8080)),
        )
