"""
src/gui/tabs/settings.py — Config editor tab.

Reads config.yaml on load, lets the user edit key fields, then writes
the YAML back and restarts the engine on Save & Restart.
"""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import filedialog
from typing import TYPE_CHECKING, Any

import customtkinter as ctk
import yaml

if TYPE_CHECKING:
    from src.gui.app import ApexTraderGUI


class SettingsTab(ctk.CTkScrollableFrame):
    def __init__(self, parent: tk.Widget, app: "ApexTraderGUI") -> None:
        super().__init__(parent, fg_color="transparent")
        self.pack(fill="both", expand=True)
        self.app = app
        self._vars: dict[str, tk.StringVar] = {}
        self._build()
        self._load()

    # ── Build form ────────────────────────────────────────────────────────────

    def _build(self) -> None:
        # ── Connection ────────────────────────────────────────────────────────
        self._section("Connection")
        self._field("Gateway WebSocket URL",    "gateway.ws_url",          "ws://localhost:4000/engine")
        self._field("Activation Key",           "gateway.activation_key",  "")
        self._field("Symbols  (comma-separated)","gateway.symbols",         "XAUUSD")

        # ── MetaTrader 5 ──────────────────────────────────────────────────────
        self._section("MetaTrader 5")
        self._field("Account Login",            "mt5.login",               "")
        self._field("Account Password",         "mt5.password",            "", masked=True)
        self._field("Server",                   "mt5.server",              "")
        self._path_field("Terminal Path",       "mt5.path")

        # ── Risk management ───────────────────────────────────────────────────
        self._section("Risk Management")
        self._field("Daily Loss Limit (%)",          "risk.max_daily_loss_percent",    "2.5")
        self._field("Max Losing Streak",             "risk.max_losing_streak",         "3")
        self._field("Equity Drawdown Limit (%)",     "risk.max_equity_drawdown_percent","2.0")
        self._field("Rolling Window  (# trades)",    "risk.rolling_window_size",       "2")
        self._field("Rolling Drawdown Limit (%)",    "risk.rolling_drawdown_pct",      "2.0")

        # ── Execution ─────────────────────────────────────────────────────────
        self._section("Execution")
        self._field("TP1 Trigger  (% of SL→TP2)",   "execution.tp1_trigger_pct",  "50.0")
        self._field("Max Signal Age  (ms)",          "execution.max_signal_age_ms","120000")
        self._field("Order Retry Count",             "execution.order_retry_count","2")

        # ── Engine ────────────────────────────────────────────────────────────
        self._section("Engine")
        self._field("Log Level  (DEBUG / INFO / WARNING)", "engine.log_level", "INFO")
        self._field("Monitoring Port",               "engine.monitoring_port",    "8080")

        # ── Footer ────────────────────────────────────────────────────────────
        self._lbl_status = ctk.CTkLabel(
            self, text="", font=ctk.CTkFont(size=12), text_color="#888",
        )
        self._lbl_status.pack(pady=(12, 4))

        ctk.CTkButton(
            self,
            text="💾  Save & Restart Engine",
            height=42,
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._save_and_restart,
            fg_color="#1a5c2a", hover_color="#22732e",
        ).pack(pady=(0, 20))

    # ── Field helpers ─────────────────────────────────────────────────────────

    def _section(self, title: str) -> None:
        ctk.CTkLabel(
            self, text=title,
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#9090ee",
        ).pack(anchor="w", padx=18, pady=(16, 4))
        ctk.CTkFrame(self, height=1, fg_color="#3a3a5a").pack(
            fill="x", padx=12, pady=(0, 8)
        )

    def _field(
        self, label: str, key: str, default: str, masked: bool = False
    ) -> None:
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=3)
        ctk.CTkLabel(
            row, text=label, width=240, anchor="w",
            font=ctk.CTkFont(size=12),
        ).pack(side="left")
        var = tk.StringVar(value=default)
        ctk.CTkEntry(
            row, textvariable=var, width=380,
            show="●" if masked else "",
            font=ctk.CTkFont(family="Consolas", size=12),
        ).pack(side="left", padx=(8, 0))
        self._vars[key] = var

    def _path_field(self, label: str, key: str) -> None:
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=3)
        ctk.CTkLabel(
            row, text=label, width=240, anchor="w",
            font=ctk.CTkFont(size=12),
        ).pack(side="left")
        var = tk.StringVar()
        ctk.CTkEntry(
            row, textvariable=var, width=300,
            font=ctk.CTkFont(family="Consolas", size=12),
        ).pack(side="left", padx=(8, 4))
        ctk.CTkButton(
            row, text="Browse…", width=80, height=28,
            command=lambda v=var: self._browse(v),
        ).pack(side="left")
        self._vars[key] = var

    # ── Load / save ───────────────────────────────────────────────────────────

    def _load(self) -> None:
        try:
            with open(self.app.config_path, "r", encoding="utf-8") as fh:
                cfg: dict = yaml.safe_load(fh) or {}
        except FileNotFoundError:
            self._lbl_status.configure(
                text="⚠  config.yaml not found — fill in values and Save.",
                text_color="#ffa502",
            )
            return
        except Exception as exc:
            self._lbl_status.configure(
                text=f"⚠  Could not load config: {exc}", text_color="#ff4757"
            )
            return

        _FIELD_MAP = {
            "gateway.ws_url":                   ("gateway",   "ws_url"),
            "gateway.activation_key":           ("gateway",   "activation_key"),
            "gateway.symbols":                  ("gateway",   "symbols"),
            "mt5.login":                        ("mt5",       "login"),
            "mt5.password":                     ("mt5",       "password"),
            "mt5.server":                       ("mt5",       "server"),
            "mt5.path":                         ("mt5",       "path"),
            "risk.max_daily_loss_percent":      ("risk",      "max_daily_loss_percent"),
            "risk.max_losing_streak":           ("risk",      "max_losing_streak"),
            "risk.max_equity_drawdown_percent": ("risk",      "max_equity_drawdown_percent"),
            "risk.rolling_window_size":         ("risk",      "rolling_window_size"),
            "risk.rolling_drawdown_pct":        ("risk",      "rolling_drawdown_pct"),
            "execution.tp1_trigger_pct":        ("execution", "tp1_trigger_pct"),
            "execution.max_signal_age_ms":      ("execution", "max_signal_age_ms"),
            "execution.order_retry_count":      ("execution", "order_retry_count"),
            "engine.log_level":                 ("engine",    "log_level"),
            "engine.monitoring_port":           ("engine",    "monitoring_port"),
        }

        for key, (section, field) in _FIELD_MAP.items():
            if key not in self._vars:
                continue
            raw = cfg.get(section, {}).get(field)
            if raw is None:
                continue
            if isinstance(raw, list):
                raw = ", ".join(str(v) for v in raw)
            self._vars[key].set(str(raw))

    def _save_and_restart(self) -> None:
        # Read current YAML
        try:
            with open(self.app.config_path, "r", encoding="utf-8") as fh:
                cfg: dict = yaml.safe_load(fh) or {}
        except Exception:
            cfg = {}

        # Apply form values
        _WRITE_MAP: dict[str, tuple[str, str, Any]] = {
            "gateway.ws_url":                   ("gateway",   "ws_url",                   str),
            "gateway.activation_key":           ("gateway",   "activation_key",           str),
            "gateway.symbols":                  ("gateway",   "symbols",                  "list"),
            "mt5.login":                        ("mt5",       "login",                    int),
            "mt5.password":                     ("mt5",       "password",                 str),
            "mt5.server":                       ("mt5",       "server",                   str),
            "mt5.path":                         ("mt5",       "path",                     str),
            "risk.max_daily_loss_percent":      ("risk",      "max_daily_loss_percent",   float),
            "risk.max_losing_streak":           ("risk",      "max_losing_streak",        int),
            "risk.max_equity_drawdown_percent": ("risk",      "max_equity_drawdown_percent", float),
            "risk.rolling_window_size":         ("risk",      "rolling_window_size",      int),
            "risk.rolling_drawdown_pct":        ("risk",      "rolling_drawdown_pct",     float),
            "execution.tp1_trigger_pct":        ("execution", "tp1_trigger_pct",          float),
            "execution.max_signal_age_ms":      ("execution", "max_signal_age_ms",        int),
            "execution.order_retry_count":      ("execution", "order_retry_count",        int),
            "engine.log_level":                 ("engine",    "log_level",                str),
            "engine.monitoring_port":           ("engine",    "monitoring_port",          int),
        }

        errors: list[str] = []
        for key, (section, field, typ) in _WRITE_MAP.items():
            if key not in self._vars:
                continue
            raw = self._vars[key].get().strip()
            if not raw:
                continue
            try:
                if typ == "list":
                    value: Any = [v.strip() for v in raw.split(",") if v.strip()]
                else:
                    value = typ(raw)
            except Exception:
                errors.append(f"{key}: invalid value '{raw}'")
                continue
            cfg.setdefault(section, {})[field] = value

        if errors:
            self._lbl_status.configure(
                text="⚠  " + " | ".join(errors), text_color=_YELLOW,
            )
            return

        # Write YAML
        try:
            with open(self.app.config_path, "w", encoding="utf-8") as fh:
                yaml.dump(cfg, fh, default_flow_style=False, allow_unicode=True, sort_keys=False)
        except Exception as exc:
            self._lbl_status.configure(
                text=f"⚠  Write failed: {exc}", text_color="#ff4757"
            )
            return

        self._lbl_status.configure(
            text="✓  Config saved — restarting engine…", text_color="#00d4aa"
        )
        threading.Thread(target=self._do_restart, daemon=True).start()

    def _do_restart(self) -> None:
        import time
        time.sleep(0.4)
        self.app.restart_with_new_config()

    @staticmethod
    def _browse(var: tk.StringVar) -> None:
        path = filedialog.askopenfilename(
            title="Select MT5 terminal64.exe",
            filetypes=[("MT5 executable", "terminal64.exe"), ("All executables", "*.exe")],
        )
        if path:
            var.set(path.replace("/", "\\"))


_YELLOW = "#ffa502"
