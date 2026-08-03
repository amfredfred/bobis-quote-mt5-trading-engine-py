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
import zlib
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


def _apply_profile_template(value: str, profile: str) -> str:
    """Substitutes a literal "{profile}" placeholder in a config value with
    this instance's own mt5.use/MT5_USE profile — e.g.
    `storage_path: ./data/{profile}` becomes `./data/fbs`. Opt-in and
    explicit (you write `{profile}` yourself), not a hidden default — one
    shared config.yaml can still be reused across per-broker instances
    (env-var-overridden MT5_USE per Task Scheduler action) without every
    instance colliding on the same storage directory."""
    if "{profile}" not in value:
        return value
    return value.replace("{profile}", profile)


# Known brokers get fixed, predictable monitoring ports so several
# instances sharing one checkout don't collide on the default — same
# reasoning and scheme as signal-engine's own per-broker websocket ports,
# kept in a distinct range (809x) so the two engines' port spaces never
# overlap even if both run on the same machine.
_KNOWN_BROKER_MONITORING_PORTS = {
    "fbs":        8091,
    "exness":     8092,
    "fundednext": 8093,
    "deriv":      8094,
}
_UNKNOWN_BROKER_MONITORING_PORT_BASE = 8100
_UNKNOWN_BROKER_MONITORING_PORT_RANGE = 200


def _default_monitoring_port(profile: str) -> int:
    key = profile.strip().lower()
    if not key:
        return 8080  # no profile — keep the historical default
    if key in _KNOWN_BROKER_MONITORING_PORTS:
        return _KNOWN_BROKER_MONITORING_PORTS[key]
    offset = zlib.crc32(key.encode()) % _UNKNOWN_BROKER_MONITORING_PORT_RANGE
    return _UNKNOWN_BROKER_MONITORING_PORT_BASE + offset


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
    return f"{interval_to_minutes(htf_interval)}/{interval_to_minutes(ltf_interval)}"


def interval_to_minutes(interval: str) -> int:
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
    # Fallback only — a signal's own limit_expiry_seconds (set by the
    # strategy that requested a limit entry, e.g. pure_crt) always wins
    # when present. This only applies when a signal somehow arrives with
    # entry_type="limit" but no expiry of its own.
    limit_order_expiry_seconds: int = 1800
    # On (default): when a resting limit order is rejected because price
    # already moved past it (MT5 retcode=10015 INVALID_PRICE), retry once
    # as the equivalent stop order (BUY_LIMIT->BUY_STOP, SELL_LIMIT->
    # SELL_STOP) at the exact same price/SL/TP/expiry, instead of just
    # skipping the signal. Off: original behavior, skip with no fallback.
    use_limit_to_stop_fallback: bool = True

    def __post_init__(self) -> None:
        _validate_pct_range("execution.tp1_trigger_pct", self.tp1_trigger_pct)
        _validate_pct_inclusive("execution.tp1_percentage", self.tp1_percentage)
        if self.limit_order_expiry_seconds <= 0:
            raise ValueError("execution.limit_order.expiry_seconds must be > 0.")
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


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base. Lists are replaced, not merged.

    Mirrors signal-engine's config/settings.py helper of the same name —
    used to apply a broker's zconfig/<broker>.yaml overlay (e.g. Deriv's
    synthetic-index symbol list) on top of the base config.yaml.
    """
    result = base.copy()
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def _load_mt5_profile(
    config_path: Path, profile: str
) -> tuple[int, str, str, str, dict, "str | None", "str | None"]:
    """Load a named MT5 credential profile from mt5-credentials.yaml.

    Returns (login, password, server, terminal_path, symbol_aliases,
    config_ref, signal_broker). Mirrors signal-engine's equivalent loader — same file name,
    same per-profile field names (including the optional symbol_aliases map
    — e.g. Exness's US100 is actually named "USTECz" on that broker's own
    MT5 platform, a rename fuzzy matching alone would never find, and the
    optional config: key pointing at a zconfig/<broker>.yaml overlay for
    broker-specific settings like Deriv's synthetic-index symbol list) —
    same "look next to the config file, then cwd" search order — so editing
    credentials for either engine feels the same, and a broker's aliases
    only need to be defined once and copied across.
    """
    candidates = [
        config_path.parent / "mt5-credentials.yaml",
        Path.cwd() / "mt5-credentials.yaml",
    ]
    creds_path = next((p for p in candidates if p.exists()), None)
    if creds_path is None:
        raise FileNotFoundError(
            f"mt5.use is {profile!r} but no mt5-credentials.yaml was found "
            f"(looked in {candidates[0].parent} and cwd)."
        )
    data = yaml.safe_load(creds_path.read_text(encoding="utf-8")) or {}
    if profile not in data:
        available = ", ".join(data.keys()) or "(none)"
        raise ValueError(
            f"MT5 credential profile {profile!r} not found in {creds_path}. "
            f"Available profiles: {available}"
        )
    p = data[profile]
    if not isinstance(p, dict):
        raise ValueError(f"Profile {profile!r} in {creds_path} must be a mapping.")
    login = p.get("login")
    password = p.get("password")
    server = p.get("server")
    terminal_path = p.get("terminal_path")
    required = [
        ("login", login), ("password", password),
        ("server", server), ("terminal_path", terminal_path),
    ]
    missing = [k for k, v in required if not v]
    if missing:
        raise ValueError(
            f"MT5 credential profile {profile!r} is missing required field(s): "
            f"{', '.join(missing)}. Add them to {creds_path}."
        )
    symbol_aliases = {
        str(k).upper(): str(v) for k, v in (p.get("symbol_aliases") or {}).items()
    }
    config_ref = p.get("config")
    # Optional: which broker's signal stream this account subscribes to,
    # when different from this profile's own name — e.g. a second Exness
    # account profiled as "exness2" (its own credentials/storage_path/
    # monitoring_port) but still trading the "exness" signal-engine
    # instance's signals. Defaults to the profile name itself, today's
    # behavior, so a single-account-per-broker setup needs nothing extra.
    signal_broker = p.get("signal_broker")
    return (
        int(login), str(password), str(server), str(terminal_path),
        symbol_aliases, str(config_ref) if config_ref else None,
        str(signal_broker) if signal_broker else None,
    )


@dataclass(frozen=True)
class Mt5Config:
    login: int
    password: str
    server: str
    path: str
    # mt5.use's profile name (e.g. "fbs") — this engine's own broker/account
    # identity, used to scope storage_path/monitoring_port per instance.
    # Empty when mt5.use is omitted (legacy flat login/server/path config).
    profile: str = ""
    # Which broker's signal stream this instance filters incoming hub
    # signals to (see SignalConsumer._own_broker). Defaults to `profile`
    # (today's behavior) but can be set independently via the credential
    # profile's optional `signal_broker` key — lets a second account on the
    # same broker (its own distinct `profile` for credentials/storage/
    # monitoring port) still subscribe to that broker's existing signal
    # stream instead of needing one named identically.
    signal_broker: str = ""
    # Canonical symbol -> broker-specific symbol name (e.g. {"US100": "USTECz"}
    # for Exness) — checked before MT5Client.resolve_symbol()'s fuzzy
    # startswith/endswith matching, same priority order as signal-engine's
    # MarketDataClient._ensure_symbol(). Empty = no overrides, unchanged
    # fuzzy-only resolution.
    symbol_aliases: dict = field(default_factory=dict)


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
    # Resolved risk_mode overlay name (RISK_MODE env var or config.yaml's
    # risk_mode: key) — e.g. "conservative", "aggressive". Empty when no
    # overlay is applied (this file's own risk: block is used as-is).
    # Purely informational at this layer; the overlay itself is already
    # merged into raw before risk/execution get parsed above.
    risk_mode: str = ""
    # Optional: also dial the local UIBridge out to a dashboard hub (see
    # src/hub), so this instance's telemetry/commands fan in alongside
    # other brokers' instead of the dashboard needing to know every
    # broker's own monitoring_port. Purely additive — monitoring_port above
    # keeps serving local direct connections unchanged either way.
    dashboard_hub_enabled: bool = False
    dashboard_hub_url: str = ""
    dashboard_hub_token: str = ""

    @classmethod
    def from_yaml(cls, path: Path | str = "config.yaml") -> "AppConfig":
        # Load .env if present — kept for backward compatibility with existing
        # installations that still have a .env file. New installs write
        # everything into config.yaml and no longer need .env.
        load_dotenv(override=False)

        config_path = Path(path)
        with open(config_path, "r", encoding="utf-8") as fh:
            raw: dict = yaml.safe_load(fh) or {}

        # Credentials: mt5.use selects a named profile from mt5-credentials.yaml
        # (git-ignored, one broker per profile — same pattern as signal-engine).
        # Omitting mt5.use keeps today's behavior: login/server/path read
        # directly from this file's mt5: block, password falling back to
        # MT5_PASSWORD env var for existing .env-based installs.
        # MT5_USE env var overrides mt5.use, same convention as signal-engine
        # — lets one checkout run multiple per-broker instances (one Task
        # Scheduler action per broker, each setting MT5_USE) without needing
        # a separate config.yaml per broker.
        #
        # Resolved before signal_engine/risk/exe/eng are sliced out below so
        # a broker's optional config: zconfig/<broker>.yaml overlay (e.g.
        # Deriv's synthetic-index symbol list — signal_engine.symbols isn't
        # one-size-fits-all across brokers) can deep-merge over raw first,
        # same order as signal-engine's _resolve_broker_cfg.
        mt5_pre = raw.get("mt5", {})
        mt5_profile = os.environ.get("MT5_USE", "").strip() or mt5_pre.get("use")
        if mt5_profile:
            (
                mt5_login, mt5_password, mt5_server, mt5_path,
                mt5_symbol_aliases, config_ref, mt5_signal_broker,
            ) = _load_mt5_profile(config_path, str(mt5_profile))
            if config_ref:
                overlay_path = config_path.parent / config_ref
                with open(overlay_path, "r", encoding="utf-8") as fh:
                    overlay: dict = yaml.safe_load(fh) or {}
                raw = _deep_merge(raw, overlay)
        else:
            mt5_login = int(_require(mt5_pre, "login", "mt5"))
            mt5_password = str(mt5_pre.get("password") or os.environ.get("MT5_PASSWORD", ""))
            if not mt5_password:
                raise ValueError(
                    "mt5.password is not set in config.yaml and MT5_PASSWORD is not "
                    "set in the environment — one of the two is required."
                )
            mt5_server = str(_require(mt5_pre, "server", "mt5"))
            mt5_path = str(mt5_pre.get("path", ""))
            mt5_symbol_aliases = {}
            mt5_signal_broker = None

        # Risk mode: an optional, broker-orthogonal overlay (zconfig/<mode>.yaml,
        # e.g. zconfig/conservative.yaml) merged on top of everything above -
        # applies regardless of which broker profile is active, and wins on
        # any risk key a broker overlay also sets (it's a deliberate, explicit
        # safety override, not a per-broker tuning value). RISK_MODE env var
        # takes priority over config.yaml's risk_mode: key, same convention as
        # MT5_USE/mt5.use.
        risk_mode = os.environ.get("RISK_MODE", "").strip().lower() or str(raw.get("risk_mode") or "").strip().lower()
        if risk_mode:
            risk_mode_path = config_path.parent / "zconfig" / f"{risk_mode}.yaml"
            if not risk_mode_path.exists():
                raise FileNotFoundError(
                    f"risk_mode={risk_mode!r} but {risk_mode_path} does not exist"
                )
            with open(risk_mode_path, "r", encoding="utf-8") as fh:
                risk_mode_overlay: dict = yaml.safe_load(fh) or {}
            raw = _deep_merge(raw, risk_mode_overlay)

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

        return cls(
            signal_engine=SignalEngineConfig(
                ws_url=str(_require(signal_engine, "ws_url", "signal_engine")),
                symbols=[
                    normalise_symbol(str(symbol)) for symbol in signal_engine_symbols_raw
                ],
            ),
            mt5=Mt5Config(
                login=mt5_login,
                password=mt5_password,
                server=mt5_server,
                path=mt5_path,
                profile=str(mt5_profile or ""),
                symbol_aliases=mt5_symbol_aliases,
                signal_broker=str(mt5_signal_broker or mt5_profile or ""),
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
                slippage=int(_require(exe, "slippage", "execution")),
                magic=int(_require(exe, "magic", "execution")),
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
                # Optional block — new, no existing config.yaml has it yet.
                limit_order_expiry_seconds=int(
                    (exe.get("limit_order") or {}).get("expiry_seconds", 1800)
                ),
                use_limit_to_stop_fallback=bool(
                    exe.get("use_limit_to_stop_fallback", True)
                ),
            ),
            storage_path=_apply_profile_template(
                str(_require(eng, "storage_path", "engine")), str(mt5_profile or "")
            ),
            log_level=str(_require(eng, "log_level", "engine")),
            position_poll_interval=float(
                _require(eng, "position_poll_interval", "engine")
            ),
            engine_timezone=ZoneInfo(str(_require(eng, "timezone", "engine"))),
            monitoring_port=int(
                eng.get("monitoring_port")
                or _default_monitoring_port(str(mt5_profile or ""))
            ),
            dashboard_hub_enabled=bool((raw.get("dashboard_hub") or {}).get("enabled", False)),
            dashboard_hub_url=str((raw.get("dashboard_hub") or {}).get("url", "") or ""),
            dashboard_hub_token=str((raw.get("dashboard_hub") or {}).get("token", "") or ""),
            risk_mode=risk_mode,
        )
