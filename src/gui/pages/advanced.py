"""
src/gui/pages/advanced.py

Advanced settings — for technical users and developers only.

Exposes settings that normal users should never need to touch:
  - Gateway WebSocket URL & activation key
  - Engine monitoring port
  - Log level
  - Signal max age, TP1 trigger, order retry count

The page starts with a clear warning that normal users should not be here.
"""
from __future__ import annotations

import threading
import tkinter as tk
from typing import TYPE_CHECKING, Any

import customtkinter as ctk

from src.gui.theme import (
    GREEN, RED, YELLOW, MUTED, TEXT,
    SURFACE_RAISED, BASE, LINE, LINE_STRONG,
    WARNING_BG, WARNING_BORDER,
    INFO_BG,
    section_rule, page_header,
)

if TYPE_CHECKING:
    from src.gui.app import ApexTraderGUI


class AdvancedPage(ctk.CTkScrollableFrame):
    def __init__(self, parent: tk.Widget, app: "ApexTraderGUI") -> None:
        super().__init__(parent, fg_color="transparent", corner_radius=0)
        self.app = app
        self._vars: dict[str, tk.StringVar] = {}
        self._build()
        self._load()

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        page_header(self, "Advanced Settings")

        content = ctk.CTkFrame(self, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=24, pady=16)

        # Warning banner
        warn = ctk.CTkFrame(
            content, corner_radius=8,
            fg_color=WARNING_BG, border_width=1, border_color=WARNING_BORDER,
        )
        warn.pack(fill="x", pady=(0, 20))
        ctk.CTkLabel(
            warn,
            text="⚠  These settings are for technical users only.\n"
                 "Incorrect values may cause the engine to stop working.",
            font=ctk.CTkFont(size=12),
            text_color=YELLOW,
            justify="left",
        ).pack(padx=16, pady=10, anchor="w")

        # ── Gateway ───────────────────────────────────────────────────────────
        section_rule(content, "Gateway Connection")

        gw_card = ctk.CTkFrame(
            content, corner_radius=8,
            fg_color=SURFACE_RAISED, border_width=1, border_color=LINE,
        )
        gw_card.pack(fill="x", pady=(0, 16))
        gw_inner = ctk.CTkFrame(gw_card, fg_color="transparent")
        gw_inner.pack(padx=24, pady=14, fill="x")

        _adv_field(gw_inner, "WebSocket URL",   "gateway.ws_url",          width=360, vars_dict=self._vars)
        _adv_field(gw_inner, "Activation Key",  "gateway.activation_key",  width=360, vars_dict=self._vars, masked=True)
        _adv_field(gw_inner, "Symbols",         "gateway.symbols",         width=280, vars_dict=self._vars,
                   hint="comma-separated, e.g. XAUUSD, US100")

        # ── Engine ────────────────────────────────────────────────────────────
        section_rule(content, "Engine")

        eng_card = ctk.CTkFrame(
            content, corner_radius=8,
            fg_color=SURFACE_RAISED, border_width=1, border_color=LINE,
        )
        eng_card.pack(fill="x", pady=(0, 16))
        eng_inner = ctk.CTkFrame(eng_card, fg_color="transparent")
        eng_inner.pack(padx=24, pady=14, fill="x")

        _adv_field(eng_inner, "Monitoring Port", "engine.monitoring_port", width=100, vars_dict=self._vars,
                   hint="Default: 8080.  Change requires reinstall.")
        _adv_field(eng_inner, "Log Level",       "engine.log_level",       width=120, vars_dict=self._vars,
                   hint="DEBUG / INFO / WARNING / ERROR")

        # ── Execution ─────────────────────────────────────────────────────────
        section_rule(content, "Execution Parameters")

        exec_card = ctk.CTkFrame(
            content, corner_radius=8,
            fg_color=SURFACE_RAISED, border_width=1, border_color=LINE,
        )
        exec_card.pack(fill="x", pady=(0, 16))
        exec_inner = ctk.CTkFrame(exec_card, fg_color="transparent")
        exec_inner.pack(padx=24, pady=14, fill="x")

        _adv_field(exec_inner, "Max Signal Age (ms)",  "execution.max_signal_age_ms",  width=110, vars_dict=self._vars,
                   hint="Signals older than this are ignored.  Default: 120000")
        _adv_field(exec_inner, "TP1 Trigger (%)",      "execution.tp1_trigger_pct",    width=80,  vars_dict=self._vars,
                   hint="% of SL→TP2 distance at which TP1 fires.  Default: 50")
        _adv_field(exec_inner, "Order Retry Count",    "execution.order_retry_count",  width=80,  vars_dict=self._vars)

        # ── Install / service ─────────────────────────────────────────────────
        section_rule(content, "Service Management")

        svc_card = ctk.CTkFrame(
            content, corner_radius=8,
            fg_color=SURFACE_RAISED, border_width=1, border_color=LINE,
        )
        svc_card.pack(fill="x", pady=(0, 16))
        svc_inner = ctk.CTkFrame(svc_card, fg_color="transparent")
        svc_inner.pack(padx=24, pady=14, fill="x")

        svc_desc = ctk.CTkLabel(
            svc_inner,
            text="Reinstall if the AQ Agent executable has changed\n"
                 "or if the task is not starting correctly.",
            font=ctk.CTkFont(size=12), text_color=MUTED,
            justify="left",
        )
        svc_desc.pack(anchor="w", pady=(0, 10))

        btn_row = ctk.CTkFrame(svc_inner, fg_color="transparent")
        btn_row.pack(anchor="w")

        ctk.CTkButton(
            btn_row, text="Reinstall Service", width=160, height=34,
            fg_color=WARNING_BG, hover_color="#2a2210",
            border_width=1, border_color=WARNING_BORDER,
            text_color=YELLOW,
            command=self._reinstall,
        ).pack(side="left", padx=(0, 10))

        self._lbl_svc_result = ctk.CTkLabel(
            btn_row, text="",
            font=ctk.CTkFont(size=11), text_color=MUTED,
        )
        self._lbl_svc_result.pack(side="left")

        # ── Save ──────────────────────────────────────────────────────────────
        self._lbl_status = ctk.CTkLabel(
            content, text="",
            font=ctk.CTkFont(size=12), text_color=MUTED,
        )
        self._lbl_status.pack(pady=(8, 6))

        ctk.CTkButton(
            content,
            text="💾  Save Advanced Settings",
            height=44, width=260,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color=INFO_BG, hover_color="#253850",
            border_width=1, border_color="#1d2c42",
            text_color="#8ab4ff",
            command=self._save,
        ).pack(pady=(0, 20))

    # ── Load / Save ───────────────────────────────────────────────────────────

    def _load(self) -> None:
        cfg = self.app.config.load(force=True)

        _MAP = {
            "gateway.ws_url":              ("gateway",   "ws_url"),
            "gateway.activation_key":      ("gateway",   "activation_key"),
            "gateway.symbols":             ("gateway",   "symbols"),
            "engine.monitoring_port":      ("engine",    "monitoring_port"),
            "engine.log_level":            ("engine",    "log_level"),
            "execution.max_signal_age_ms": ("execution", "max_signal_age_ms"),
            "execution.tp1_trigger_pct":   ("execution", "tp1_trigger_pct"),
            "execution.order_retry_count": ("execution", "order_retry_count"),
        }
        for key, (section, field) in _MAP.items():
            if key not in self._vars:
                continue
            raw = cfg.get(section, {}).get(field)
            if raw is None:
                continue
            if isinstance(raw, list):
                raw = ", ".join(str(v) for v in raw)
            self._vars[key].set(str(raw))

    def _save(self) -> None:
        _WRITE_MAP: dict[str, tuple[str, str, Any]] = {
            "gateway.ws_url":              ("gateway",   "ws_url",              str),
            "gateway.activation_key":      ("gateway",   "activation_key",      str),
            "gateway.symbols":             ("gateway",   "symbols",             "list"),
            "engine.monitoring_port":      ("engine",    "monitoring_port",     int),
            "engine.log_level":            ("engine",    "log_level",           str),
            "execution.max_signal_age_ms": ("execution", "max_signal_age_ms",   int),
            "execution.tp1_trigger_pct":   ("execution", "tp1_trigger_pct",     float),
            "execution.order_retry_count": ("execution", "order_retry_count",   int),
        }
        errors: list[str] = []
        cfg = self.app.config.load()

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
                errors.append(f"'{key}' invalid: '{raw}'")
                continue
            cfg.setdefault(section, {})[field] = value

        if errors:
            self._lbl_status.configure(
                text="⚠  " + "  |  ".join(errors), text_color=YELLOW,
            )
            return

        err = self.app.config.save(cfg)
        if err:
            self._lbl_status.configure(text=f"⚠  {err}", text_color=RED)
            return

        self._lbl_status.configure(
            text="✓  Saved — restarting AQ Agent…", text_color=GREEN,
        )
        self.app.app_state.mark_setup_complete(self.app.config.is_setup_complete())
        threading.Thread(target=self._delayed_restart, daemon=True).start()

    def _delayed_restart(self) -> None:
        import time
        time.sleep(0.4)
        self.app.restart_with_new_config()

    def _reinstall(self) -> None:
        self._lbl_svc_result.configure(text="Reinstalling…", text_color=YELLOW)
        self.app.installer.on_result = lambda ok, msg: self.after(
            0,
            lambda: self._lbl_svc_result.configure(
                text=msg[:80], text_color=GREEN if ok else RED,
            ),
        )
        self.app.installer.reinstall_async(str(self.app.config.path))

    def on_engine_status(self, status: str, detail: str | None) -> None:
        from src.gui.service_controller import ServiceStatus
        if status == ServiceStatus.STOPPED and detail:
            self._lbl_svc_result.configure(
                text=detail[:80], text_color=MUTED,
            )
        elif status == ServiceStatus.RUNNING:
            self._lbl_svc_result.configure(text="")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _adv_field(
    parent: tk.Widget,
    label: str,
    key: str,
    width: int,
    vars_dict: dict[str, tk.StringVar],
    hint: str = "",
    masked: bool = False,
) -> None:
    row = ctk.CTkFrame(parent, fg_color="transparent")
    row.pack(fill="x", pady=3)

    ctk.CTkLabel(
        row, text=label, width=200, anchor="w",
        font=ctk.CTkFont(size=12), text_color=TEXT,
    ).pack(side="left")

    var = tk.StringVar()
    vars_dict[key] = var

    ctk.CTkEntry(
        row, textvariable=var, width=width,
        show="●" if masked else "",
        font=ctk.CTkFont(family="Consolas", size=12),
    ).pack(side="left", padx=(8, 8))

    if hint:
        ctk.CTkLabel(
            row, text=hint,
            font=ctk.CTkFont(size=10), text_color=MUTED, anchor="w",
        ).pack(side="left")
