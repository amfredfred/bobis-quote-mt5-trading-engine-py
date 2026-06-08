"""
src/gui/pages/engine.py

Engine control page.

Shows live engine runtime status (service state, last heartbeat, uptime)
and provides Start / Stop / Restart controls.
"""
from __future__ import annotations

import time
import tkinter as tk
from typing import TYPE_CHECKING

import customtkinter as ctk

if TYPE_CHECKING:
    from src.gui.app import ApexTraderGUI

_GREEN   = "#00d4aa"
_RED     = "#ff4757"
_YELLOW  = "#ffa502"
_MUTED   = "#6b6b8a"
_TEXT    = "#e0e0e0"
_CARD_BG = "#111128"


class EnginePage(ctk.CTkFrame):
    def __init__(self, parent: tk.Widget, app: "ApexTraderGUI") -> None:
        super().__init__(parent, fg_color="transparent", corner_radius=0)
        self.app = app
        self._current_status = "unknown"
        self._ws_connected   = False
        self._build()
        # Start heartbeat ticker
        self._tick()

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        # Header
        hdr = ctk.CTkFrame(self, height=52, fg_color=_CARD_BG, corner_radius=0)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        ctk.CTkLabel(
            hdr, text="Engine",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(side="left", padx=20)

        content = ctk.CTkScrollableFrame(self, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=24, pady=16)

        # ── Status card ───────────────────────────────────────────────────────
        status_card = ctk.CTkFrame(content, corner_radius=10)
        status_card.pack(fill="x", pady=(0, 16))

        inner = ctk.CTkFrame(status_card, fg_color="transparent")
        inner.pack(padx=20, pady=16, fill="x")

        # Big status label
        self._lbl_status_dot = ctk.CTkLabel(
            inner,
            text="●  Checking…",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color=_MUTED,
        )
        self._lbl_status_dot.pack(anchor="w")

        # Sub-info row
        info_row = ctk.CTkFrame(inner, fg_color="transparent")
        info_row.pack(fill="x", pady=(8, 0))

        self._lbl_heartbeat = ctk.CTkLabel(
            info_row, text="Last signal: --",
            font=ctk.CTkFont(size=12), text_color=_MUTED,
        )
        self._lbl_heartbeat.pack(side="left", padx=(0, 24))

        self._lbl_gateway = ctk.CTkLabel(
            info_row, text="Dashboard: Disconnected",
            font=ctk.CTkFont(size=12), text_color=_MUTED,
        )
        self._lbl_gateway.pack(side="left")

        # ── Action buttons ────────────────────────────────────────────────────
        _section_label(content, "Controls")

        btn_card = ctk.CTkFrame(content, corner_radius=10)
        btn_card.pack(fill="x", pady=(0, 16))

        btn_inner = ctk.CTkFrame(btn_card, fg_color="transparent")
        btn_inner.pack(padx=20, pady=16)

        self._btn_start = ctk.CTkButton(
            btn_inner, text="▶  Start Engine", width=160, height=42,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#1a5c2a", hover_color="#22732e",
            command=self._start,
        )
        self._btn_start.grid(row=0, column=0, padx=8, pady=4)

        self._btn_stop = ctk.CTkButton(
            btn_inner, text="■  Stop Engine", width=160, height=42,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#5c1a1a", hover_color="#7a2222",
            command=self._stop,
        )
        self._btn_stop.grid(row=0, column=1, padx=8, pady=4)

        self._btn_restart = ctk.CTkButton(
            btn_inner, text="↺  Restart Engine", width=160, height=42,
            font=ctk.CTkFont(size=13),
            fg_color="#2d4a6e", hover_color="#3d5a7e",
            command=self._restart,
        )
        self._btn_restart.grid(row=0, column=2, padx=8, pady=4)

        self._lbl_action_result = ctk.CTkLabel(
            content, text="",
            font=ctk.CTkFont(size=12), text_color=_MUTED,
        )
        self._lbl_action_result.pack(pady=(0, 16))

        # ── Error panel ───────────────────────────────────────────────────────
        self._error_card = ctk.CTkFrame(content, corner_radius=10, fg_color="#3a1010")
        # Only shown when there's an error

        self._error_lbl = ctk.CTkLabel(
            self._error_card, text="",
            font=ctk.CTkFont(size=12), text_color="#ffaaaa",
            wraplength=600, justify="left",
        )
        self._error_lbl.pack(padx=20, pady=14, anchor="w")

        # ── Technical details (collapsible) ───────────────────────────────────
        _section_label(content, "Technical Details")

        tech_card = ctk.CTkFrame(content, corner_radius=10)
        tech_card.pack(fill="x", pady=(0, 16))

        tech_inner = ctk.CTkFrame(tech_card, fg_color="transparent")
        tech_inner.pack(padx=20, pady=14, fill="x")

        _info_row(tech_inner, "Service name", "apex-quant-trader-agent")
        _info_row(tech_inner, "Service manager", "NSSM")
        _info_row(tech_inner, "Control method", "Windows Service (sc.exe)")
        _info_row(tech_inner, "UIBridge", "ws://localhost:8080  (configurable)")

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def on_engine_status(self, status: str, detail: str | None) -> None:
        from src.gui.service_controller import ServiceStatus

        self._current_status = status

        _dot_colours = {
            ServiceStatus.NOT_INSTALLED: _MUTED,
            ServiceStatus.STOPPED:       _RED,
            ServiceStatus.STARTING:      _YELLOW,
            ServiceStatus.RUNNING:       _GREEN,
            ServiceStatus.STOPPING:      _YELLOW,
            ServiceStatus.UNKNOWN:       _MUTED,
        }
        _dot_labels = {
            ServiceStatus.NOT_INSTALLED: "Not Installed",
            ServiceStatus.STOPPED:       "Stopped",
            ServiceStatus.STARTING:      "Starting…",
            ServiceStatus.RUNNING:       "Running",
            ServiceStatus.STOPPING:      "Stopping…",
            ServiceStatus.UNKNOWN:       "Unknown",
        }
        label = _dot_labels.get(status, status)
        color = _dot_colours.get(status, _MUTED)
        self._lbl_status_dot.configure(
            text=f"●  Engine {label}", text_color=color,
        )

        # Update button states
        is_running  = status == ServiceStatus.RUNNING
        is_stopped  = status in (ServiceStatus.STOPPED, ServiceStatus.NOT_INSTALLED)
        is_busy     = status in (ServiceStatus.STARTING, ServiceStatus.STOPPING)

        self._btn_start.configure(state="normal" if is_stopped else "disabled")
        self._btn_stop.configure(state="normal" if is_running else "disabled")
        self._btn_restart.configure(state="normal" if is_running else "disabled")

        if is_stopped and detail:
            self._show_error(detail)
        elif not is_stopped:
            self._hide_error()

        if is_busy:
            self._lbl_action_result.configure(text="Please wait…", text_color=_YELLOW)
        else:
            self._lbl_action_result.configure(text="")

    def on_ws_connected(self) -> None:
        self._ws_connected = True
        self._lbl_gateway.configure(
            text="Dashboard: Connected", text_color=_GREEN,
        )

    def on_ws_disconnected(self) -> None:
        self._ws_connected = False
        self._lbl_gateway.configure(
            text="Dashboard: Disconnected", text_color=_MUTED,
        )

    def on_mt5_error(self, message: str) -> None:
        if ":" in message:
            message = message.split(":", 1)[-1].strip()
        self._show_error(f"MT5: {message}")

    # ── Heartbeat ticker ──────────────────────────────────────────────────────

    def _tick(self) -> None:
        last = self.app._last_heartbeat
        if last > 0:
            secs = int(time.time() - last)
            if secs < 5:
                txt = "Last signal: just now"
            elif secs < 120:
                txt = f"Last signal: {secs}s ago"
            else:
                txt = f"Last signal: {secs // 60}m ago"
            color = _GREEN if secs < 15 else _YELLOW if secs < 60 else _RED
            self._lbl_heartbeat.configure(text=txt, text_color=color)
        else:
            self._lbl_heartbeat.configure(text="Last signal: --", text_color=_MUTED)
        self.after(1000, self._tick)

    # ── Button actions ────────────────────────────────────────────────────────

    def _start(self) -> None:
        from src.gui.service_controller import ServiceStatus
        if self.app.svc.query() == ServiceStatus.NOT_INSTALLED:
            self._lbl_action_result.configure(
                text="Service not installed — go to Advanced to install.", text_color=_YELLOW
            )
            return
        self._lbl_action_result.configure(text="Starting…", text_color=_YELLOW)
        self.app.svc.start()

    def _stop(self) -> None:
        self._lbl_action_result.configure(text="Stopping…", text_color=_YELLOW)
        self.app.svc.stop()

    def _restart(self) -> None:
        self._lbl_action_result.configure(text="Restarting…", text_color=_YELLOW)
        self.app.svc.restart()

    # ── Error panel ───────────────────────────────────────────────────────────

    def _show_error(self, msg: str) -> None:
        self._error_lbl.configure(
            text=f"⚠  {msg}\n\nYou can restart the engine using the controls above."
        )
        self._error_card.pack(fill="x", pady=(0, 16))

    def _hide_error(self) -> None:
        self._error_card.pack_forget()


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


def _info_row(parent: tk.Widget, label: str, value: str) -> None:
    row = ctk.CTkFrame(parent, fg_color="transparent")
    row.pack(fill="x", pady=2)
    ctk.CTkLabel(
        row, text=label, width=180, anchor="w",
        font=ctk.CTkFont(size=12), text_color=_MUTED,
    ).pack(side="left")
    ctk.CTkLabel(
        row, text=value, anchor="w",
        font=ctk.CTkFont(family="Consolas", size=12), text_color=_TEXT,
    ).pack(side="left")
