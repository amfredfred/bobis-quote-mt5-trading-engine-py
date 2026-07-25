"""
hub/config.py — execution dashboard hub process settings.

YAML-first, env-var overrides — same convention as signal-engine's own
hub/config.py and execution-engine's own config.yaml. hub-config.yaml sits
next to config.yaml at the execution-engine repo root (copy
hub-config.example.yaml to get started).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config file root must be a mapping: {path}")
    return data


def _get(data: dict, key: str, default: Any = None) -> Any:
    cur: Any = data
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def _config_path() -> Path:
    return Path(os.getenv("HUB_CONFIG", str(Path.cwd() / "hub-config.yaml")))


@dataclass
class HubConfig:
    # Internal listener — execution-engine instances' forwarders dial into
    # this. Localhost-only; never expose this publicly.
    internal_host: str = "127.0.0.1"
    internal_port: int = 8095

    # Public listener — the dashboard connects here. Same default port
    # (8080) UIBridge used for a single instance, since every instance's
    # own local monitoring_port now defaults to a distinct per-broker value
    # (8091/8092/8093) instead — 8080 is free for the hub to take over.
    public_host: str = "0.0.0.0"
    public_port: int = 8080

    secret: str = ""
    max_clients: int = 50

    log_dir: str = "logs/hub"
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "HubConfig":
        cfg = _load_yaml(_config_path())
        return cls(
            internal_host=os.getenv("HUB_INTERNAL_HOST", str(_get(cfg, "internal.host", "127.0.0.1"))),
            internal_port=int(os.getenv("HUB_INTERNAL_PORT", str(_get(cfg, "internal.port", 8095)))),
            public_host=os.getenv("HUB_PUBLIC_HOST", str(_get(cfg, "public.host", "0.0.0.0"))),
            public_port=int(os.getenv("HUB_PUBLIC_PORT", str(_get(cfg, "public.port", 8080)))),
            secret=os.getenv("HUB_SECRET", str(_get(cfg, "secret", "") or "")),
            max_clients=int(os.getenv("HUB_MAX_CLIENTS", str(_get(cfg, "max_clients", 50)))),
            log_dir=os.getenv("HUB_LOG_DIR", str(_get(cfg, "log.dir", "logs/hub"))),
            log_level=os.getenv("HUB_LOG_LEVEL", str(_get(cfg, "log.level", "INFO"))),
        )
