"""
src/gui/pages/activity.py — Activity / Event log

Replaces logs.py.  Two tabs:
  • Events   — structured trade/signal events with icons
  • Raw logs — tail of the engine log file
"""
from __future__ import annotations

import os
import threading
import time
import tkinter as tk
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING

import customtkinter as ctk

from src.gui.theme import (
    GREEN, RED, YELLOW, INFO, MUTED, TEXT, TEXT_SOFT,
    BASE, SURFACE, SURFACE_RAISED, LINE, LINE_STRONG,
    SUCCESS_BG, DANGER_BG, WARNING_BG, INFO_BG,
    SUCCESS_BORDER, DANGER_BORDER, WARNING_BORDER, INFO_BORDER,
    section_rule, page_header,
)
from src.gui.components import SectionCard, PrimaryButton

if TYPE_CHECKING:
    from src.gui.app import ApexTraderGUI

_MAX_EVENTS = 200
_MAX_LINES  = 2000

_EVENT_META = {
    # (icon, tone_bg, tone_border, text_color)
    "trade.opened":    ("📈", SUCCESS_BG,  SUCCESS_BORDER,  GREEN),
    "trade.tp1_hit":   ("✓",  SUCCESS_BG,  SUCCESS_BORDER,  GREEN),
    "trade.tp2_hit":   ("✓✓", SUCCESS_BG,  SUCCESS_BORDER,  GREEN),
    "trade.sl_hit":    ("✕",  DANGER_BG,   DANGER_BORDER,   RED),
    "trade.closed":    ("■",  SURFACE_RAISED, LINE,          MUTED),
    "signal.received": ("📡", INFO_BG,     INFO_BORDER,     INFO),
    "signal.triggered":("🎯", INFO_BG,     INFO_BORDER,     INFO),
    "risk.approved":   ("✅", SUCCESS_BG,  SUCCESS_BORDER,  GREEN),
    "risk.rejected":   ("🚫", WARNING_BG,  WARNING_BORDER,  YELLOW),
    "ws_connected":    ("🔗", SUCCESS_BG,  SUCCESS_BORDER,  GREEN),
    "ws_disconnected": ("🔌", DANGER_BG,   DANGER_BORDER,   RED),
    "mt5_error":       ("⚠",  DANGER_BG,   DANGER_BORDER,   RED),
}


class ActivityPage(ctk.CTkFrame):

    def __init__(self, parent: tk.Widget, app: "ApexTraderGUI") -> None:
        super().__init__(parent, fg_color=SURFACE, corner_radius=0)
        self.app = app
        self._events: deque = deque(maxlen=_MAX_EVENTS)
        self._log_lines: deque = deque(maxlen=_MAX_LINES)
        self._autoscroll = True
        self._build()
        self._start_log_tail()

    # ── Layout ─────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        # page_header packs itself at the top
        page_header(self, "Activity", "Engine events and log output")

        # Tab bar below the header
        tab_bar = ctk.CTkFrame(self, fg_color=SURFACE_RAISED, corner_radius=0, height=40)
        tab_bar.pack(fill="x")
        tab_bar.pack_propagate(False)

        self._tab_events = ctk.CTkButton(
            tab_bar, text="Events", width=120, height=38,
            corner_radius=0,
            fg_color=SUCCESS_BG, text_color=GREEN,
            font=ctk.CTkFont(size=13, weight="bold"),
            command=lambda: self._show_tab("events"),
        )
        self._tab_events.pack(side="left")

        self._tab_logs = ctk.CTkButton(
            tab_bar, text="Raw Logs", width=120, height=38,
            corner_radius=0,
            fg_color="transparent", text_color=MUTED,
            font=ctk.CTkFont(size=13),
            command=lambda: self._show_tab("logs"),
        )
        self._tab_logs.pack(side="left")

        ctk.CTkButton(
            tab_bar, text="Clear", width=80, height=30,
            fg_color="transparent", hover_color=LINE,
            border_width=1, border_color=LINE,
            text_color=MUTED, font=ctk.CTkFont(size=11),
            command=self._clear,
        ).pack(side="right", padx=12, pady=4)

        self._autoscroll_var = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            tab_bar, text="Auto-scroll",
            variable=self._autoscroll_var,
            font=ctk.CTkFont(size=11), text_color=MUTED,
            checkbox_width=16, checkbox_height=16,
            command=lambda: setattr(self, "_autoscroll", self._autoscroll_var.get()),
        ).pack(side="right", padx=4)

        # Stacked content frames (lifted on tab switch)
        content = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        content.pack(fill="both", expand=True)

        # Events tab — scrollable, packed to fill content
        self._events_frame = ctk.CTkScrollableFrame(
            content, fg_color=BASE, corner_radius=0,
        )
        self._events_frame.place(relx=0, rely=0, relwidth=1.0, relheight=1.0)

        self._no_events_lbl = ctk.CTkLabel(
            self._events_frame,
            text="No events yet — engine events will appear here once running.",
            font=ctk.CTkFont(size=12), text_color=MUTED,
        )
        self._no_events_lbl.pack(pady=32)

        # Logs tab — textbox fills content
        self._logs_frame = ctk.CTkFrame(content, fg_color=BASE, corner_radius=0)
        self._logs_frame.place(relx=0, rely=0, relwidth=1.0, relheight=1.0)

        self._log_box = ctk.CTkTextbox(
            self._logs_frame,
            font=ctk.CTkFont(family="Consolas", size=11),
            fg_color="#060810", text_color=TEXT_SOFT,
            corner_radius=0, wrap="none",
            state="disabled",
        )
        self._log_box.pack(fill="both", expand=True)

        # Colour tags for log levels
        self._log_box.tag_config("DEBUG",   foreground=MUTED)
        self._log_box.tag_config("INFO",    foreground=TEXT_SOFT)
        self._log_box.tag_config("WARNING", foreground=YELLOW)
        self._log_box.tag_config("ERROR",   foreground=RED)
        self._log_box.tag_config("CRITICAL",foreground=RED)

        self._show_tab("events")

    def _show_tab(self, tab: str) -> None:
        self._active_tab = tab
        if tab == "events":
            self._events_frame.lift()
            self._tab_events.configure(fg_color=SUCCESS_BG, text_color=GREEN)
            self._tab_logs.configure(fg_color="transparent", text_color=MUTED)
        else:
            self._logs_frame.lift()
            self._tab_logs.configure(fg_color=SUCCESS_BG, text_color=GREEN)
            self._tab_events.configure(fg_color="transparent", text_color=MUTED)

    # ── Event rendering ────────────────────────────────────────────────────────

    def _add_event(self, event_type: str, payload: dict) -> None:
        meta = _EVENT_META.get(event_type, ("•", SURFACE_RAISED, LINE, MUTED))
        icon, bg, border, color = meta

        if self._no_events_lbl.winfo_exists():
            self._no_events_lbl.pack_forget()

        ts   = time.strftime("%H:%M:%S")
        card = ctk.CTkFrame(
            self._events_frame,
            fg_color=bg, border_width=1, border_color=border, corner_radius=6,
        )
        card.pack(fill="x", padx=8, pady=3)

        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=7)

        ctk.CTkLabel(
            row, text=icon, width=28,
            font=ctk.CTkFont(size=14), text_color=color,
        ).pack(side="left")

        ctk.CTkLabel(
            row, text=event_type.replace(".", " → "),
            font=ctk.CTkFont(size=12, weight="bold"), text_color=color, anchor="w",
        ).pack(side="left", padx=(4, 8))

        # Brief summary from payload
        summary = _summarise(event_type, payload)
        if summary:
            ctk.CTkLabel(
                row, text=summary,
                font=ctk.CTkFont(size=11), text_color=TEXT_SOFT, anchor="w",
            ).pack(side="left", fill="x", expand=True)

        ctk.CTkLabel(
            row, text=ts,
            font=ctk.CTkFont(family="Consolas", size=10), text_color=MUTED,
        ).pack(side="right")

        self._events.append(card)
        if self._autoscroll:
            self.after(50, lambda: self._events_frame._parent_canvas.yview_moveto(1.0))

    # ── Log file tail ──────────────────────────────────────────────────────────

    def _start_log_tail(self) -> None:
        threading.Thread(target=self._tail_log, daemon=True).start()

    def _tail_log(self) -> None:
        log_path = self._find_log()
        if not log_path:
            return
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
                # Seek to end
                fh.seek(0, os.SEEK_END)
                while True:
                    line = fh.readline()
                    if line:
                        self._log_lines.append(line.rstrip())
                        self.after(0, lambda l=line.rstrip(): self._append_log_line(l))
                    else:
                        time.sleep(0.4)
        except Exception:
            pass

    def _find_log(self) -> Path | None:
        from src.gui.config_manager import ConfigManager
        candidates = [
            ConfigManager.programdata_logs_path() / "engine.log",
            ConfigManager.programdata_logs_path() / "apex.log",
        ]
        import sys
        exe_dir = Path(sys.executable).parent
        candidates += [
            exe_dir / "logs" / "engine.log",
            exe_dir / "data"  / "engine.log",
        ]
        for p in candidates:
            if p.exists():
                return p
        return None

    def _append_log_line(self, line: str) -> None:
        level = "INFO"
        for lvl in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            if lvl in line:
                level = lvl
                break
        self._log_box.configure(state="normal")
        self._log_box.insert("end", line + "\n", level)
        self._log_box.configure(state="disabled")
        if self._autoscroll:
            self._log_box.see("end")

    def _clear(self) -> None:
        if self._active_tab == "events":
            for w in self._events_frame.winfo_children():
                if w is not self._no_events_lbl:
                    w.destroy()
            self._no_events_lbl.pack(pady=32)
        else:
            self._log_box.configure(state="normal")
            self._log_box.delete("1.0", "end")
            self._log_box.configure(state="disabled")

    # ── Broadcast callbacks ────────────────────────────────────────────────────

    def on_trade_event(self, event_type: str, payload: dict) -> None:
        self._add_event(event_type, payload)

    def on_signal_event(self, event_type: str, payload: dict) -> None:
        self._add_event(event_type, payload)

    def on_ws_connected(self) -> None:
        self._add_event("ws_connected", {})

    def on_ws_disconnected(self) -> None:
        self._add_event("ws_disconnected", {})

    def on_mt5_error(self, message: str) -> None:
        self._add_event("mt5_error", {"message": message})

    def on_engine_status(self, status: str, detail=None) -> None:
        pass


# ── Payload summariser ─────────────────────────────────────────────────────────

def _summarise(event_type: str, payload: dict) -> str:
    if not payload:
        return ""
    if event_type == "trade.opened":
        sym   = payload.get("symbol", "")
        side  = payload.get("side", "")
        lots  = payload.get("lots", "")
        return f"{sym}  {side}  {lots} lots" if sym else ""
    if event_type in ("trade.sl_hit", "trade.tp1_hit", "trade.tp2_hit"):
        sym = payload.get("symbol", payload.get("trade_id", ""))
        pnl = payload.get("pnl") or payload.get("profit")
        return f"{sym}  P&L: {pnl:+.2f}" if pnl is not None else str(sym)
    if event_type == "mt5_error":
        return payload.get("message", "")[:80]
    if event_type in ("signal.received", "signal.triggered"):
        sym  = payload.get("symbol", "")
        side = payload.get("side", "")
        return f"{sym} {side}".strip()
    return ""
