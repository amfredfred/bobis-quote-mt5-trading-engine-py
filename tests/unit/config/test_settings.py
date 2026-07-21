"""MT5 credential resolution: mt5.use profile loading + legacy fallback."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.config.settings import AppConfig, _load_mt5_profile


# Everything from config.example.yaml except the mt5: block, which each test
# supplies itself (either `use: <profile>` or direct credentials) - keeps
# every test exercising a complete, realistic config rather than a stub with
# just enough fields to not crash.
_BASE_CONFIG_YAML = """
signal_engine:
  ws_url: ws://localhost:8765
  symbols:
    - XAUUSD
risk:
  max_losing_streak: 20
  max_daily_loss_percent: 100
  max_exposure_per_symbol: 2
  min_rr_ratio: 1.0
  max_lot_size: 100.0
  min_lot_size: 0.01
  sl_ratio_threshold: 0.35
  symbol_sl_ratio_threshold: {{}}
  no_hedging: true
  max_profit_drawdown_percent: 2.0
  rolling_window_size: 2
  rolling_drawdown_pct: 2.0
execution:
  magic: 8858
  slippage: 10
  tp1_trigger_pct: 45.0
  tp1_percentage: 0.0
  move_sl_to_be_on_tp1: true
  breakeven_spread_multiplier: 1
  breakeven_max_buffer_pct_of_risk: 10.0
  spread_risk_multiplier: 1.0
  order_retry_count: 2
  order_retry_delay_sec: 0.5
  max_entry_slippage_pct_of_stop: 0.2
  max_signal_age_ms: 120000
  close_on_slippage_exceed: false
  adjust_levels_on_slippage: false
engine:
  timezone: UTC
  log_level: INFO
  storage_path: ./data
  monitoring_port: 8080
  position_poll_interval: 0.6
{mt5_block}
"""


def _write_config(tmp_path: Path, mt5_block: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(_BASE_CONFIG_YAML.format(mt5_block=mt5_block), encoding="utf-8")
    return path


# ── _load_mt5_profile ────────────────────────────────────────────────────────

def test_load_mt5_profile_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "mt5-credentials.yaml").write_text(
        "fbs:\n"
        "  login: 106272844\n"
        '  password: "secret"\n'
        '  server: "FBS-Demo"\n'
        '  terminal_path: "C:\\\\MT5\\\\terminal64.exe"\n',
        encoding="utf-8",
    )
    login, password, server, terminal_path = _load_mt5_profile(
        tmp_path / "config.yaml", "fbs"
    )
    assert login == 106272844
    assert password == "secret"
    assert server == "FBS-Demo"
    assert terminal_path == "C:\\MT5\\terminal64.exe"


def test_load_mt5_profile_missing_file_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Neither candidate path (config dir, cwd) has the file - chdir to
    # tmp_path so the cwd fallback can't accidentally find this repo's own
    # real mt5-credentials.yaml and mask the case under test.
    monkeypatch.chdir(tmp_path)
    with pytest.raises(FileNotFoundError, match="mt5-credentials.yaml"):
        _load_mt5_profile(tmp_path / "config.yaml", "fbs")


def test_load_mt5_profile_missing_profile_lists_available(tmp_path: Path) -> None:
    (tmp_path / "mt5-credentials.yaml").write_text(
        'fbs:\n  login: 1\n  password: "x"\n  server: "s"\n  terminal_path: "p"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Available profiles: fbs"):
        _load_mt5_profile(tmp_path / "config.yaml", "fundednext")


def test_load_mt5_profile_missing_field_names_it(tmp_path: Path) -> None:
    (tmp_path / "mt5-credentials.yaml").write_text(
        'fbs:\n  login: 1\n  server: "s"\n  terminal_path: "p"\n',  # no password
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="password"):
        _load_mt5_profile(tmp_path / "config.yaml", "fbs")


# ── AppConfig.from_yaml wiring ────────────────────────────────────────────────

def test_from_yaml_resolves_credentials_via_profile(tmp_path: Path) -> None:
    (tmp_path / "mt5-credentials.yaml").write_text(
        "fbs:\n"
        "  login: 106272844\n"
        '  password: "secret"\n'
        '  server: "FBS-Demo"\n'
        '  terminal_path: "C:\\\\MT5\\\\terminal64.exe"\n',
        encoding="utf-8",
    )
    config_path = _write_config(tmp_path, "mt5:\n  use: fbs\n")

    cfg = AppConfig.from_yaml(config_path)

    assert cfg.mt5.login == 106272844
    assert cfg.mt5.password == "secret"
    assert cfg.mt5.server == "FBS-Demo"
    assert cfg.mt5.path == "C:\\MT5\\terminal64.exe"
    # magic/slippage now come from execution:, not mt5: - regression check
    # for the parser fix bundled into this same migration.
    assert cfg.execution.magic == 8858
    assert cfg.execution.slippage == 10


def test_from_yaml_legacy_direct_credentials_still_work(tmp_path: Path) -> None:
    """mt5.use omitted - today's exact pre-migration behavior must keep
    working unmodified (no mt5-credentials.yaml needed for this path)."""
    mt5_block = (
        "mt5:\n"
        "  login: 111\n"
        '  password: "legacy-secret"\n'
        '  server: "Legacy-Demo"\n'
        '  path: "C:\\\\Legacy\\\\terminal64.exe"\n'
    )
    config_path = _write_config(tmp_path, mt5_block)

    cfg = AppConfig.from_yaml(config_path)

    assert cfg.mt5.login == 111
    assert cfg.mt5.password == "legacy-secret"
    assert cfg.mt5.server == "Legacy-Demo"
    assert cfg.mt5.path == "C:\\Legacy\\terminal64.exe"
