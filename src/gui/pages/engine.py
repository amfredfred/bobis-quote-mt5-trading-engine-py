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

from src.gui.theme import (
    GREEN, RED, YELLOW, MUTED, TEXT, TEXT_SOFT,
    SURFACE_RAISED, LINE, LINE_STRONG,
    DANGER_BG, INFO_BG, SUCCESS_BG, SUCCESS_BORDER,
    section_rule, info_row, page_header,
)

if TYPE_CHECKING:
    from src.gui.app import ApexTraderGUI


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
        page_header(self, "Engine")

        content = ctk.CTkScrollableFrame(self, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=24, pady=16)

        # ── Status card ───────────────────────────────────────────────────────
        status_card = ctk.CTkFrame(
            content, corner_radius=8,
            fg_color=SURFACE_RAISED, border_width=1, border_color=LINE,
        )
        status_card.pack(fill="x", pady=(0, 16))

        inner = ctk.CTkFrame(status_card, fg_color="transparent")
        inner.pack(padx=20, pady=16, fill="x")

        # Big status label
        self._lbl_status_dot = ctk.CTkLabel(
            inner,
            text="●  Checking…",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color=MUTED,
        )
        self._lbl_status_dot.pack(anchor="w")

        # Sub-info row
        sub_row = ctk.CTkFrame(inner, fg_color="transparent")
        sub_row.pack(fill="x", pady=(10, 0))

        self._lbl_heartbeat = ctk.CTkLabel(
            sub_row, text="Last signal: --",
            font=ctk.CTkFont(size=12), text_color=MUTED,
        )
        self._lbl_heartbeat.pack(side="left", padx=(0, 24))

        self._lbl_gateway = ctk.CTkLabel(
            sub_row, text="Dashboard: Disconnected",
            font=ctk.CTkFont(size=12), text_color=MUTED,
        )
        self._lbl_gateway.pack(side="left")

        # ── Action buttons ────────────────────────────────────────────────────
        section_rule(content, "Controls")

        btn_card = ctk.CTkFrame(
            content, corner_radius=8,
            fg_color=SURFACE_RAISED, border_width=1, border_color=LINE,
        )
        btn_card.pack(fill="x", pady=(0, 16))

        btn_inner = ctk.CTkFrame(btn_card, fg_color="transparent")
        btn_inner.pack(padx=20, pady=16)

        self._btn_start = ctk.CTkButton(
            btn_inner, text="▶  Start Engine", width=160, height=44,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color=SUCCESS_BG, hover_color=SUCCESS_BORDER,
            border_width=1, border_color=SUCCESS_BORDER,
            text_color=GREEN,
            command=self._start,
        )
        self._btn_start.grid(row=0, column=0, padx=8, pady=4)

        self._btn_stop = ctk.CTkButton(
            btn_inner, text="■  Stop Engine", width=160, height=44,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color=DANGER_BG, hover_color="#5a1e2a",
            border_width=1, border_color="#38141e",
            text_color=RED,
            command=self._stop,
        )
        self._btn_stop.grid(row=0, column=1, padx=8, pady=4)

        self._btn_restart = ctk.CTkButton(
            btn_inner, text="↺  Restart Engine", width=160, height=44,
            font=ctk.CTkFont(size=13),
            fg_color=INFO_BG, hover_color="#253850",
            border_width=1, border_color="#1d2c42",
            text_color="#8ab4ff",
            command=self._restart,
        )
        self._btn_restart.grid(row=0, column=2, padx=8, pady=4)

        self._lbl_action_result = ctk.CTkLabel(
            content, text="",
            font=ctk.CTkFont(size=12), text_color=MUTED,
        )
        self._lbl_action_result.pack(pady=(0, 16))

        # ── Error panel ───────────────────────────────────────────────────────
        self._error_card = ctk.CTkFrame(
            content, corner_radius=8,
            fg_color=DANGER_BG, border_width=1, border_color="#38141e",
        )
        # Only shown when there's an error

        self._error_lbl = ctk.CTkLabel(
            self._error_card, text="",
            font=ctk.CTkFont(size=12), text_color="#ffb3bd",
            wraplength=600, justify="left",
        )
        self._error_lbl.pack(padx=20, pady=14, anchor="w")

        # ── Technical details ─────────────────────────────────────────────────
        section_rule(content, "Technical Details")

        tech_card = ctk.CTkFrame(
            content, corner_radius=8,
            fg_color=SURFACE_RAISED, border_width=1, border_color=LINE,
        )
        tech_card.pack(fill="x", pady=(0, 16))

        tech_inner = ctk.CTkFrame(tech_card, fg_color="transparent")
        tech_inner.pack(padx=20, pady=14, fill="x")

        info_row(tech_inner, "Service name",    "apex-quant-trader-agent")
        info_row(tech_inner, "Service manager", "NSSM")
        info_row(tech_inner, "Control method",  "Windows Service (sc.exe)")
        info_row(tech_inner, "UIBridge",        "ws://localhost:8080  (configurable)")

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def on_engine_status(self, status: str, detail: str | None) -> None:
        from src.gui.service_controller import ServiceStatus

        self._current_status = status

        _dot_colours = {
            ServiceStatus.NOT_INSTALLED: MUTED,
            ServiceStatus.STOPPED:       RED,
            ServiceStatus.STARTING:      YELLOW,
            ServiceStatus.RUNNING:       GREEN,
            ServiceStatus.STOPPING:      YELLOW,
            ServiceStatus.UNKNOWN:       MUTED,
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
        color = _dot_colours.get(status, MUTED)
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
            self._lbl_action_result.configure(text="Please wait…", text_color=YELLOW)
        else:
            self._lbl_action_result.configure(text="")

    def on_ws_connected(self) -> None:
        self._ws_connected = True
        self._lbl_gateway.configure(
            text="Dashboard: Connected", text_color=GREEN,
        )

    def on_ws_disconnected(self) -> None:
        self._ws_connected = False
        self._lbl_gateway.configure(
            text="Dashboard: Disconnected", text_color=MUTED,
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
            color = GREEN if secs < 15 else YELLOW if secs < 60 else RED
            self._lbl_heartbeat.configure(text=txt, text_color=color)
        else:
            self._lbl_heartbeat.configure(text="Last signal: --", text_color=MUTED)
        self.after(1000, self._tick)

    # ── Button actions ────────────────────────────────────────────────────────

    def _start(self) -> None:
        from src.gui.service_controller import ServiceStatus
        if self.app.svc.query() == ServiceStatus.NOT_INSTALLED:
            self._lbl_action_result.configure(
                text="Service not installed — go to Advanced to install.", text_color=YELLOW
            )
            return
        self._lbl_action_result.configure(text="Starting…", text_color=YELLOW)
        self.app.svc.start()

    def _stop(self) -> None:
        self._lbl_action_result.configure(text="Stopping…", text_color=YELLOW)
        self.app.svc.stop()

    def _restart(self) -> None:
        self._lbl_action_result.configure(text="Restarting…", text_color=YELLOW)
        self.app.svc.restart()

    # ── Error panel ───────────────────────────────────────────────────────────

    def _show_error(self, msg: str) -> None:
        self._error_lbl.configure(
            text=f"⚠  {msg}\n\nYou can restart the engine using the controls above."
        )
        self._error_card.pack(fill="x", pady=(0, 16))

    def _hide_error(self) -> None:
        self._error_card.pack_forget()
