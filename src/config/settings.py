"""
Application configuration — loaded from config.yaml at startup.

Every value comes from config.yaml — there are no hidden internal defaults.
If a required key is missing, from_yaml() raises a clear error naming the
exact config.yaml path that needs to be filled in. See config.example.yaml
for a fully-populated reference file.

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


def _require(mapping: dict, key: str, section: str) -> Any:
    """Fetch a required key or raise a clear, actionable config error."""
    if key not in mapping:
        raise ValueError(
            f"config.yaml is missing required key: {section}.{key}  "
            f"(see config.example.yaml for a fully-populated reference)"
        )
    return mapping[key]


def _validate_pct_range(name: str, value: float) -> None:
    if value <= 0.0 or value >= 100.0:
        raise ValueError(f"{name} must be > 0 and < 100.")


def _validate_pct_inclusive(name: str, value: float) -> None:
    if value < 0.0 or value > 100.0:
        raise ValueError(f"{name} must be between 0 and 100.")


def _parse_tf_overrides(raw: Any) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """Parse per-symbol, per-timeframe TP1 overrides.

    YAML form:
      XAUUSD:
        "5/5":
          tp1_trigger_pct: 45.0
        "*":              # wildcard TF for this symbol
          tp1_trigger_pct: 42.0
      "*":                # wildcard symbol
        "5/5":
          tp1_trigger_pct: 40.0

    Resolution priority: symbol+TF > symbol+* > *+TF > *+* > global default.
    Symbol keys are uppercased; "*" is kept as-is.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("execution.tf_overrides must be a mapping.")

    result: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for raw_symbol, tf_map in raw.items():
        if not isinstance(tf_map, dict):
            raise ValueError(
                f"execution.tf_overrides.{raw_symbol} must be a mapping."
            )
        symbol = str(raw_symbol)
        if symbol != "*":
            symbol = symbol.upper().replace("/", "")
        parsed_tf: Dict[str, Dict[str, Any]] = {}
        for pair, values in tf_map.items():
            if not isinstance(values, dict):
                raise ValueError(
                    f"execution.tf_overrides.{raw_symbol}.{pair} must be a mapping."
                )
            override: Dict[str, Any] = {}
            if "tp1_trigger_pct" in values:
                override["tp1_trigger_pct"] = float(values["tp1_trigger_pct"])
            if "tp1_percentage" in values:
                override["tp1_percentage"] = float(values["tp1_percentage"])
            if "tp1_close_pct" in values:
                override["tp1_percentage"] = float(values["tp1_close_pct"])
            if override:
                parsed_tf[str(pair)] = override
        if parsed_tf:
            result[symbol] = parsed_tf
    return result


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
class ClusterGroupConfig:
    name: str
    symbols: tuple[str, ...]
    max_same_day_loss_r: float = 1.5
    max_concurrent_positions: int = 2
    max_same_day_losses: int = 2
    after_first_loss_risk_multiplier: float = 0.5
    min_trade_risk_multiplier: float = 0.25

    def __post_init__(self) -> None:
        if self.max_same_day_loss_r <= 0:
            raise ValueError("max_same_day_loss_r must be > 0")
        if self.max_concurrent_positions < 1:
            raise ValueError("max_concurrent_positions must be >= 1")
        if self.max_same_day_losses < 1:
            raise ValueError("max_same_day_losses must be >= 1")
        if not (0 < self.after_first_loss_risk_multiplier <= 1):
            raise ValueError("after_first_loss_risk_multiplier must be > 0 and <= 1")
        if not (0 < self.min_trade_risk_multiplier <= 1):
            raise ValueError("min_trade_risk_multiplier must be > 0 and <= 1")


@dataclass(frozen=True)
class ClusterRiskConfig:
    enabled: bool = False
    groups: tuple[ClusterGroupConfig, ...] = ()


@dataclass(frozen=True)
class EquityThrottleConfig:
    """Equity-curve risk throttle — platform-internal except `enabled`.

    Sizes new positions at `risk_multiplier` while the rolling R-equity of
    closed trades sits more than `drawdown_threshold_r` below its window
    peak; releases once drawdown recovers below `release_threshold_r`.
    """

    enabled: bool = True
    drawdown_threshold_r: float = 8.0
    release_threshold_r: float = 6.0
    risk_multiplier: float = 0.5
    window_days: int = 30

    def __post_init__(self) -> None:
        if self.drawdown_threshold_r <= 0:
            raise ValueError("risk.equity_throttle.drawdown_threshold_r must be > 0.")
        if not (0 < self.release_threshold_r <= self.drawdown_threshold_r):
            raise ValueError(
                "risk.equity_throttle.release_threshold_r must be > 0 and "
                "<= drawdown_threshold_r."
            )
        if not (0 < self.risk_multiplier <= 1.0):
            raise ValueError(
                "risk.equity_throttle.risk_multiplier must be in (0, 1]."
            )
        if self.window_days < 1:
            raise ValueError("risk.equity_throttle.window_days must be >= 1.")


@dataclass(frozen=True)
class EntryDriftConfig:
    """Entry-drift diagnostic/gate — separate from the min_rr_rule floor.

    min_rr_rule recomputes R:R from the live fill price while leaving the
    signal's SL/TP unchanged; a rejection there conflates "the setup decayed"
    with "the entry side moved against the spread." This rule measures how
    far the live fill price has moved AGAINST the signal's own entry (a
    favorable move — e.g. a pullback before a LONG fills — never counts, no
    matter how large), as a percentage of the signal's own risk distance,
    and — only when enabled — rejects on that basis with a distinct,
    diagnosable reason. The drift measurement itself is always recorded (via
    risk-rejection metrics) even while disabled, so it has diagnostic value
    from day one.
    """

    enabled: bool = False
    max_drift_pct_of_risk: float = 25.0

    def __post_init__(self) -> None:
        if self.max_drift_pct_of_risk <= 0:
            raise ValueError(
                "risk.entry_drift.max_drift_pct_of_risk must be > 0."
            )


def _parse_entry_drift(raw: Any) -> "EntryDriftConfig":
    if not raw:
        # Block omitted entirely = feature off (diagnostic recording still
        # runs unconditionally inside the rule itself; only the reject
        # decision is gated on `enabled`).
        return EntryDriftConfig(enabled=False)
    if not isinstance(raw, dict):
        raise ValueError("risk.entry_drift must be a mapping.")
    return EntryDriftConfig(
        enabled=bool(_require(raw, "enabled", "risk.entry_drift")),
        max_drift_pct_of_risk=float(
            _require(raw, "max_drift_pct_of_risk", "risk.entry_drift")
        ),
    )


def _parse_equity_throttle(raw: Any) -> "EquityThrottleConfig":
    if not raw:
        # Block omitted entirely = feature off. Values are inert while
        # disabled but must still satisfy __post_init__'s validation.
        return EquityThrottleConfig(
            enabled=False,
            drawdown_threshold_r=1.0,
            release_threshold_r=1.0,
            risk_multiplier=1.0,
            window_days=1,
        )
    if not isinstance(raw, dict):
        raise ValueError("risk.equity_throttle must be a mapping.")
    return EquityThrottleConfig(
        enabled=bool(_require(raw, "enabled", "risk.equity_throttle")),
        drawdown_threshold_r=float(
            _require(raw, "drawdown_threshold_r", "risk.equity_throttle")
        ),
        release_threshold_r=float(
            _require(raw, "release_threshold_r", "risk.equity_throttle")
        ),
        risk_multiplier=float(_require(raw, "risk_multiplier", "risk.equity_throttle")),
        window_days=int(_require(raw, "window_days", "risk.equity_throttle")),
    )


def _parse_cluster_risk(raw: Any) -> "ClusterRiskConfig":
    if not raw:
        return ClusterRiskConfig(enabled=False)
    if not isinstance(raw, dict):
        raise ValueError("risk.cluster_risk must be a mapping.")

    groups_raw = raw.get("groups", [])
    if groups_raw is None:
        groups_raw = []
    if not isinstance(groups_raw, list):
        raise ValueError("risk.cluster_risk.groups must be a list.")

    groups: list[ClusterGroupConfig] = []
    for item in groups_raw:
        if not isinstance(item, dict):
            raise ValueError("Each risk.cluster_risk.groups item must be a mapping.")

        symbols_raw = item.get("symbols", [])
        if isinstance(symbols_raw, str):
            symbols_raw = [s.strip() for s in symbols_raw.split(",") if s.strip()]
        if not symbols_raw:
            raise ValueError("Cluster group must include at least one symbol.")

        group = ClusterGroupConfig(
            name=str(item["name"]),
            symbols=tuple(normalise_symbol(str(s)) for s in symbols_raw),
            max_same_day_loss_r=float(
                _require(item, "max_same_day_loss_r", "risk.cluster_risk.groups[]")
            ),
            max_concurrent_positions=int(
                _require(item, "max_concurrent_positions", "risk.cluster_risk.groups[]")
            ),
            max_same_day_losses=int(
                _require(item, "max_same_day_losses", "risk.cluster_risk.groups[]")
            ),
            after_first_loss_risk_multiplier=float(
                _require(
                    item, "after_first_loss_risk_multiplier", "risk.cluster_risk.groups[]"
                )
            ),
            min_trade_risk_multiplier=float(
                _require(item, "min_trade_risk_multiplier", "risk.cluster_risk.groups[]")
            ),
        )
        groups.append(group)

    return ClusterRiskConfig(
        enabled=bool(_require(raw, "enabled", "risk.cluster_risk")),
        groups=tuple(groups),
    )


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
    symbol_risk_multiplier: Dict[str, float]
    no_hedging: bool = True
    max_profit_drawdown_percent: float = 2.0
    rolling_window_size: int = 0
    rolling_drawdown_pct: float = 0.0
    cluster_risk: ClusterRiskConfig = field(default_factory=ClusterRiskConfig)
    equity_throttle: EquityThrottleConfig = field(default_factory=EquityThrottleConfig)
    entry_drift: EntryDriftConfig = field(default_factory=EntryDriftConfig)
    # Balance-tiered risk ceiling: caps risk_amount at balance * cap_pct/100,
    # where cap_pct is base_cap_pct for any balance <= base_threshold, then
    # halves for every doubling past base_threshold down to floor_pct. A
    # ceiling on top of the existing daily-budget sizing, not a replacement -
    # see TradePlanner.plan() and LossTracker.balance_tier_cap_pct().
    balance_tier_base_threshold: float = 500.0
    balance_tier_base_cap_pct: float = 5.0
    balance_tier_floor_pct: float = 0.05

    def __post_init__(self) -> None:
        if self.max_losing_streak < 1:
            raise ValueError(
                f"risk.max_losing_streak must be >= 1, got: {self.max_losing_streak}"
            )
        if self.balance_tier_base_threshold <= 0:
            raise ValueError(
                "risk.balance_tier_base_threshold must be > 0, got: "
                f"{self.balance_tier_base_threshold}"
            )
        if self.balance_tier_floor_pct <= 0 or self.balance_tier_floor_pct > self.balance_tier_base_cap_pct:
            raise ValueError(
                "risk.balance_tier_floor_pct must be > 0 and <= "
                f"balance_tier_base_cap_pct, got floor={self.balance_tier_floor_pct} "
                f"base_cap={self.balance_tier_base_cap_pct}"
            )
        for symbol, multiplier in self.symbol_risk_multiplier.items():
            if multiplier < 0:
                raise ValueError(
                    f"risk.symbol_risk_multiplier[{symbol!r}] must be >= 0, "
                    f"got: {multiplier}"
                )


@dataclass(frozen=True)
class ExecutionConfig:
    tp1_trigger_pct: float   # 0–100: TP1 fires at this % of the entry→TP2 range
    tp1_percentage: float
    move_sl_to_be_on_tp1: bool
    slippage: int
    magic: int
    spread_risk_multiplier: float
    order_retry_count: int
    max_entry_slippage_pct_of_stop: float
    close_on_slippage_exceed: bool
    order_retry_delay_sec: float
    breakeven_spread_multiplier: float = 1.5
    breakeven_max_buffer_pct_of_risk: float = 10.0
    adjust_levels_on_slippage: bool = False
    max_signal_age_ms: int = 90_000
    tf_overrides: Dict[str, Dict[str, Dict[str, Any]]] = field(default_factory=dict)

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
        for sym, tf_map in self.tf_overrides.items():
            for tf_key, override in tf_map.items():
                if "tp1_trigger_pct" in override:
                    _validate_pct_range(
                        f"execution.tf_overrides.{sym}.{tf_key}.tp1_trigger_pct",
                        float(override["tp1_trigger_pct"]),
                    )
                if "tp1_percentage" in override:
                    _validate_pct_inclusive(
                        f"execution.tf_overrides.{sym}.{tf_key}.tp1_percentage",
                        float(override["tp1_percentage"]),
                    )

    def _resolve_tf_override(
        self, symbol: str | None, tf_key: str | None
    ) -> Dict[str, Any]:
        """Return the most-specific override dict for (symbol, tf_key).

        Priority: symbol+TF > symbol+* > *+TF > *+* > {} (no override).
        """
        if not self.tf_overrides:
            return {}
        norm = symbol.upper().replace("/", "") if symbol else None
        candidates: list[tuple[str | None, str | None]] = []
        if norm and tf_key:
            candidates.append((norm, tf_key))
        if norm:
            candidates.append((norm, "*"))
        if tf_key:
            candidates.append(("*", tf_key))
        candidates.append(("*", "*"))
        for sym, tf in candidates:
            if sym is None:
                continue
            sym_map = self.tf_overrides.get(sym)
            if not sym_map:
                continue
            entry = sym_map.get(tf or "*", {})
            if entry:
                return dict(entry)
        return {}

    def tp1_trigger_pct_for(
        self,
        symbol: str | None,
        htf_interval: str | None,
        ltf_interval: str | None,
    ) -> float:
        tf_key = _tf_pair_key(htf_interval, ltf_interval)
        override = self._resolve_tf_override(symbol, tf_key)
        return float(override.get("tp1_trigger_pct", self.tp1_trigger_pct))

    def tp1_percentage_for(
        self,
        symbol: str | None,
        htf_interval: str | None,
        ltf_interval: str | None,
    ) -> float:
        tf_key = _tf_pair_key(htf_interval, ltf_interval)
        override = self._resolve_tf_override(symbol, tf_key)
        return float(override.get("tp1_percentage", self.tp1_percentage))


@dataclass(frozen=True)
class Mt5Config:
    login: int
    password: str
    server: str
    path: str


@dataclass(frozen=True)
class SignalEngineConfig:
    ws_url: str
    symbols: list[str]

    def __post_init__(self) -> None:
        if not self.symbols:
            raise ValueError("signal_engine.symbols must contain at least one symbol.")


@dataclass(frozen=True)
class AppConfig:
    risk: RiskConfig
    execution: ExecutionConfig
    mt5: Mt5Config
    signal_engine: SignalEngineConfig
    storage_path: str
    log_level: str
    position_poll_interval: float
    engine_timezone: ZoneInfo
    monitoring_port: int = 8080

    @classmethod
    def from_yaml(cls, path: Path | str = "config.yaml") -> "AppConfig":
        # Load .env if present — kept for backward compatibility with existing
        # installations that still have a .env file. New installs write
        # everything into config.yaml and no longer need .env.
        load_dotenv(override=False)

        with open(path, "r", encoding="utf-8") as fh:
            raw: dict = yaml.safe_load(fh) or {}

        # "signal_engine" is the current key; "gateway" is accepted as a
        # fallback so existing config.yaml files from before this engine
        # connected directly to the signal engine keep working unmodified.
        signal_engine = raw.get("signal_engine") or raw.get("gateway") or {}
        mt5 = raw.get("mt5", {})
        risk = raw.get("risk", {})
        exe = raw.get("execution", {})
        eng = raw.get("engine", {})

        signal_engine_symbols_raw = _require(signal_engine, "symbols", "signal_engine")
        if isinstance(signal_engine_symbols_raw, str):
            signal_engine_symbols_raw = [
                s.strip() for s in signal_engine_symbols_raw.split(",")
            ]

        # Secret: prefer config.yaml, fall back to MT5_PASSWORD env var so
        # existing .env-based installs don't need to put a password in a
        # committed file. One of the two must be set — this is the only
        # value in this file allowed to come from outside config.yaml.
        mt5_password = str(mt5.get("password") or os.environ.get("MT5_PASSWORD", ""))
        if not mt5_password:
            raise ValueError(
                "mt5.password is not set in config.yaml and MT5_PASSWORD is not "
                "set in the environment — one of the two is required."
            )

        return cls(
            signal_engine=SignalEngineConfig(
                ws_url=str(_require(signal_engine, "ws_url", "signal_engine")),
                symbols=[
                    normalise_symbol(str(symbol)) for symbol in signal_engine_symbols_raw
                ],
            ),
            mt5=Mt5Config(
                login=int(_require(mt5, "login", "mt5")),
                password=mt5_password,
                server=str(_require(mt5, "server", "mt5")),
                path=str(mt5.get("path", "")),
            ),
            risk=RiskConfig(
                max_losing_streak=int(_require(risk, "max_losing_streak", "risk")),
                max_daily_loss_percent=float(
                    _require(risk, "max_daily_loss_percent", "risk")
                ),
                max_exposure_per_symbol=int(
                    _require(risk, "max_exposure_per_symbol", "risk")
                ),
                min_rr_ratio=float(_require(risk, "min_rr_ratio", "risk")),
                max_lot_size=float(_require(risk, "max_lot_size", "risk")),
                min_lot_size=float(_require(risk, "min_lot_size", "risk")),
                sl_ratio_threshold=float(_require(risk, "sl_ratio_threshold", "risk")),
                # Per-symbol override map — omitting it means no per-symbol
                # overrides, which is a structural choice, not a defaulted value.
                symbol_sl_ratio_threshold={
                    normalise_symbol(str(symbol)): float(threshold)
                    for symbol, threshold in risk.get(
                        "symbol_sl_ratio_threshold", {}
                    ).items()
                },
                # Per-symbol risk_amount multiplier, applied alongside the
                # cluster/equity-throttle multipliers in TradePlanner.plan().
                # Omitting a symbol (or the whole map) means 1.0 - no change
                # from today's flat, symbol-agnostic sizing.
                symbol_risk_multiplier={
                    normalise_symbol(str(symbol)): float(multiplier)
                    for symbol, multiplier in risk.get(
                        "symbol_risk_multiplier", {}
                    ).items()
                },
                no_hedging=bool(_require(risk, "no_hedging", "risk")),
                max_profit_drawdown_percent=float(
                    _require(risk, "max_profit_drawdown_percent", "risk")
                ),
                rolling_window_size=int(_require(risk, "rolling_window_size", "risk")),
                rolling_drawdown_pct=float(
                    _require(risk, "rolling_drawdown_pct", "risk")
                ),
                # Structural feature blocks: omitted entirely = feature off.
                cluster_risk=_parse_cluster_risk(risk.get("cluster_risk")),
                equity_throttle=_parse_equity_throttle(risk.get("equity_throttle")),
                entry_drift=_parse_entry_drift(risk.get("entry_drift")),
                # Balance-tiered risk ceiling - optional, defaults preserve
                # today's behavior if omitted entirely from config.yaml.
                balance_tier_base_threshold=float(
                    risk.get("balance_tier_base_threshold", 500.0)
                ),
                balance_tier_base_cap_pct=float(
                    risk.get("balance_tier_base_cap_pct", 5.0)
                ),
                balance_tier_floor_pct=float(
                    risk.get("balance_tier_floor_pct", 0.05)
                ),
            ),
            execution=ExecutionConfig(
                tp1_trigger_pct=float(_require(exe, "tp1_trigger_pct", "execution")),
                tp1_percentage=float(_require(exe, "tp1_percentage", "execution")),
                move_sl_to_be_on_tp1=bool(
                    _require(exe, "move_sl_to_be_on_tp1", "execution")
                ),
                slippage=int(_require(mt5, "slippage", "mt5")),
                magic=int(_require(mt5, "magic", "mt5")),
                spread_risk_multiplier=float(
                    _require(exe, "spread_risk_multiplier", "execution")
                ),
                order_retry_count=int(_require(exe, "order_retry_count", "execution")),
                max_entry_slippage_pct_of_stop=float(
                    _require(exe, "max_entry_slippage_pct_of_stop", "execution")
                ),
                close_on_slippage_exceed=bool(
                    _require(exe, "close_on_slippage_exceed", "execution")
                ),
                order_retry_delay_sec=float(
                    _require(exe, "order_retry_delay_sec", "execution")
                ),
                breakeven_spread_multiplier=float(
                    _require(exe, "breakeven_spread_multiplier", "execution")
                ),
                breakeven_max_buffer_pct_of_risk=float(
                    _require(exe, "breakeven_max_buffer_pct_of_risk", "execution")
                ),
                adjust_levels_on_slippage=bool(
                    _require(exe, "adjust_levels_on_slippage", "execution")
                ),
                max_signal_age_ms=int(_require(exe, "max_signal_age_ms", "execution")),
                # Structural: omitted entirely = no per-symbol/timeframe overrides.
                tf_overrides=_parse_tf_overrides(exe.get("tf_overrides")),
            ),
            storage_path=str(_require(eng, "storage_path", "engine")),
            log_level=str(_require(eng, "log_level", "engine")),
            position_poll_interval=float(
                _require(eng, "position_poll_interval", "engine")
            ),
            engine_timezone=ZoneInfo(str(_require(eng, "timezone", "engine"))),
            monitoring_port=int(eng.get("monitoring_port", 8080)),
        )
