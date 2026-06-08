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

if TYPE_CHECKING:
    from src.gui.app import ApexTraderGUI

_GREEN   = "#00d4aa"
_RED     = "#ff4757"
_YELLOW  = "#ffa502"
_MUTED   = "#6b6b8a"
_TEXT    = "#e0e0e0"
_CARD_BG = "#111128"


class AdvancedPage(ctk.CTkScrollableFrame):
    def __init__(self, parent: tk.Widget, app: "ApexTraderGUI") -> None:
        super().__init__(parent, fg_color="transparent", corner_radius=0)
        self.app = app
        self._vars: dict[str, tk.StringVar] = {}
        self._build()
        self._load()

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        hdr = ctk.CTkFrame(self, height=52, fg_color=_CARD_BG, corner_radius=0)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        ctk.CTkLabel(
            hdr, text="Advanced Settings",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(side="left", padx=20)

        content = ctk.CTkFrame(self, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=24, pady=16)

        # Warning banner
        warn = ctk.CTkFrame(content, corner_radius=8, fg_color="#2a1a00")
        warn.pack(fill="x", pady=(0, 20))
        ctk.CTkLabel(
            warn,
            text="⚠  These settings are for technical users only.\n"
                 "Incorrect values may cause the engine to stop working.",
            font=ctk.CTkFont(size=12),
            text_color="#ffcc66",
            justify="left",
        ).pack(padx=16, pady=10, anchor="w")

        # ── Gateway ───────────────────────────────────────────────────────────
        _section_label(content, "Gateway Connection")

        gw_card = ctk.CTkFrame(content, corner_radius=10)
        gw_card.pack(fill="x", pady=(0, 16))
        gw_inner = ctk.CTkFrame(gw_card, fg_color="transparent")
        gw_inner.pack(padx=24, pady=14, fill="x")

        _adv_field(gw_inner, "WebSocket URL",   "gateway.ws_url",          width=360, vars_dict=self._vars)
        _adv_field(gw_inner, "Activation Key",  "gateway.activation_key",  width=360, vars_dict=self._vars, masked=True)
        _adv_field(gw_inner, "Symbols",         "gateway.symbols",         width=280, vars_dict=self._vars,
                   hint="comma-separated, e.g. XAUUSD, US100")

        # ── Engine ────────────────────────────────────────────────────────────
        _section_label(content, "Engine")

        eng_card = ctk.CTkFrame(content, corner_radius=10)
        eng_card.pack(fill="x", pady=(0, 16))
        eng_inner = ctk.CTkFrame(eng_card, fg_color="transparent")
        eng_inner.pack(padx=24, pady=14, fill="x")

        _adv_field(eng_inner, "Monitoring Port", "engine.monitoring_port", width=100, vars_dict=self._vars,
                   hint="Default: 8080.  Change requires reinstall.")
        _adv_field(eng_inner, "Log Level",       "engine.log_level",       width=120, vars_dict=self._vars,
                   hint="DEBUG / INFO / WARNING / ERROR")

        # ── Execution ─────────────────────────────────────────────────────────
        _section_label(content, "Execution Parameters")

        exec_card = ctk.CTkFrame(content, corner_radius=10)
        exec_card.pack(fill="x", pady=(0, 16))
        exec_inner = ctk.CTkFrame(exec_card, fg_color="transparent")
        exec_inner.pack(padx=24, pady=14, fill="x")

        _adv_field(exec_inner, "Max Signal Age (ms)",  "execution.max_signal_age_ms",  width=110, vars_dict=self._vars,
                   hint="Signals older than this are ignored.  Default: 120000")
        _adv_field(exec_inner, "TP1 Trigger (%)",      "execution.tp1_trigger_pct",    width=80,  vars_dict=self._vars,
                   hint="% of SL→TP2 distance at which TP1 fires.  Default: 50")
        _adv_field(exec_inner, "Order Retry Count",    "execution.order_retry_count",  width=80,  vars_dict=self._vars)

        # ── Install / service ─────────────────────────────────────────────────
        _section_label(content, "Service Management")

        svc_card = ctk.CTkFrame(content, corner_radius=10)
        svc_card.pack(fill="x", pady=(0, 16))
        svc_inner = ctk.CTkFrame(svc_card, fg_color="transparent")
        svc_inner.pack(padx=24, pady=14, fill="x")

        svc_desc = ctk.CTkLabel(
            svc_inner,
            text="Reinstall the service if the engine executable has changed\n"
                 "or if the service is not starting correctly.",
            font=ctk.CTkFont(size=12), text_color=_MUTED,
            justify="left",
        )
        svc_desc.pack(anchor="w", pady=(0, 10))

        btn_row = ctk.CTkFrame(svc_inner, fg_color="transparent")
        btn_row.pack(anchor="w")

        ctk.CTkButton(
            btn_row, text="Reinstall Service", width=160, height=34,
            fg_color="#3a1a00", hover_color="#5a3000",
            command=self._reinstall,
        ).pack(side="left", padx=(0, 10))

        self._lbl_svc_result = ctk.CTkLabel(
            btn_row, text="",
            font=ctk.CTkFont(size=11), text_color=_MUTED,
        )
        self._lbl_svc_result.pack(side="left")

        # ── Save ──────────────────────────────────────────────────────────────
        self._lbl_status = ctk.CTkLabel(
            content, text="",
            font=ctk.CTkFont(size=12), text_color=_MUTED,
        )
        self._lbl_status.pack(pady=(8, 6))

        ctk.CTkButton(
            content,
            text="💾  Save Advanced Settings",
            height=42, width=260,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#2d4a6e", hover_color="#3d5a7e",
            command=self._save,
        ).pack(pady=(0, 20))

    # ── Load / Save ───────────────────────────────────────────────────────────

    def _load(self) -> None:
        cfg = self.app.load_config()

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

        try:
            cfg = self.app.load_config()
        except Exception:
            cfg = {}

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
                text="⚠  " + "  |  ".join(errors), text_color=_YELLOW,
            )
            return

        try:
            self.app.save_config(cfg)
        except Exception as exc:
            self._lbl_status.configure(
                text=f"⚠  Write failed: {exc}", text_color=_RED,
            )
            return

        self._lbl_status.configure(
            text="✓  Saved — restarting engine…", text_color=_GREEN,
        )
        threading.Thread(target=self._delayed_restart, daemon=True).start()

    def _delayed_restart(self) -> None:
        import time
        time.sleep(0.4)
        self.app.restart_with_new_config()

    def _reinstall(self) -> None:
        self._lbl_svc_result.configure(text="Installing…", text_color=_YELLOW)
        self.app.svc.install(self.app.config_path)

    def on_engine_status(self, status: str, detail: str | None) -> None:
        from src.gui.service_controller import ServiceStatus
        if status == ServiceStatus.STOPPED and detail:
            self._lbl_svc_result.configure(
                text=detail[:80], text_color=_MUTED,
            )
        elif status == ServiceStatus.RUNNING:
            self._lbl_svc_result.configure(text="")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _section_label(parent: tk.Widget, text: str) -> None:
    ctk.CTkLabel(
        parent, text=text,
        font=ctk.CTkFont(size=12, weight="bold"),
        text_color="#7070aa",
    ).pack(anchor="w", pady=(0, 6))
    ctk.CTkFrame(parent, height=1, fg_color="#2a2a4a").pack(
        fill="x", pady=(0, 10)
    )


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
        font=ctk.CTkFont(size=12),
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
            font=ctk.CTkFont(size=10), text_color=_MUTED, anchor="w",
        ).pack(side="left")
