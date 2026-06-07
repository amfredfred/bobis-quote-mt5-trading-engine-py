"""
src/gui/tabs/logs.py — Live log viewer.

Receives log records via append_log() (called from the GUI poll loop, so
always on the main thread).  Colour-codes by level.
"""
from __future__ import annotations

import tkinter as tk
from typing import TYPE_CHECKING

import customtkinter as ctk

if TYPE_CHECKING:
    from src.gui.app import ApexTraderGUI

_LEVEL_COLOURS = {
    "DEBUG":    "#666688",
    "INFO":     "#cccccc",
    "WARNING":  "#ffa502",
    "ERROR":    "#ff4757",
    "CRITICAL": "#ff1a2e",
}


class LogsTab(ctk.CTkFrame):
    def __init__(self, parent: tk.Widget, app: "ApexTraderGUI") -> None:
        super().__init__(parent, fg_color="transparent")
        self.pack(fill="both", expand=True)
        self.app = app
        self._auto_scroll = True
        self._build()

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        toolbar = ctk.CTkFrame(self, fg_color="transparent", height=36)
        toolbar.pack(fill="x", padx=10, pady=(6, 2))
        toolbar.pack_propagate(False)

        self._auto_scroll_var = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            toolbar, text="Auto-scroll",
            variable=self._auto_scroll_var,
            command=self._on_auto_scroll_toggle,
            width=110,
        ).pack(side="left", padx=4)

        ctk.CTkButton(
            toolbar, text="Clear", width=70, height=28,
            command=self._clear,
        ).pack(side="left", padx=4)

        # Level filter
        ctk.CTkLabel(toolbar, text="Level:", font=ctk.CTkFont(size=11)).pack(
            side="left", padx=(12, 4)
        )
        self._level_var = tk.StringVar(value="DEBUG")
        ctk.CTkOptionMenu(
            toolbar,
            values=["DEBUG", "INFO", "WARNING", "ERROR"],
            variable=self._level_var,
            width=100, height=28,
        ).pack(side="left")

        # Log text
        self._text = ctk.CTkTextbox(
            self,
            wrap="none",
            font=ctk.CTkFont(family="Consolas", size=11),
            activate_scrollbars=True,
        )
        self._text.pack(fill="both", expand=True, padx=10, pady=(2, 10))
        self._text.configure(state="disabled")

        # Configure level colours on the underlying tk.Text widget
        tw = self._text._textbox
        for level, colour in _LEVEL_COLOURS.items():
            tw.tag_configure(level, foreground=colour)

    # ── Public API (called on GUI main thread) ────────────────────────────────

    def append_log(self, entry: dict) -> None:
        level = entry.get("level", "INFO")
        if not self._level_visible(level):
            return

        msg = entry.get("msg", "")
        tw  = self._text._textbox

        self._text.configure(state="normal")
        tw.insert("end", msg + "\n", level)
        self._text.configure(state="disabled")

        if self._auto_scroll:
            tw.see("end")

    def append_logs_bulk(self, entries: list[dict]) -> None:
        if not entries:
            return
        tw = self._text._textbox
        self._text.configure(state="normal")
        for entry in entries:
            level = entry.get("level", "INFO")
            if self._level_visible(level):
                tw.insert("end", entry.get("msg", "") + "\n", level)
        self._text.configure(state="disabled")
        if self._auto_scroll:
            tw.see("end")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _level_visible(self, level: str) -> bool:
        _RANK = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}
        min_rank = _RANK.get(self._level_var.get(), 0)
        return _RANK.get(level, 0) >= min_rank

    def _on_auto_scroll_toggle(self) -> None:
        self._auto_scroll = self._auto_scroll_var.get()

    def _clear(self) -> None:
        self._text.configure(state="normal")
        self._text.delete("0.0", "end")
        self._text.configure(state="disabled")
