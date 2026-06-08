"""
src/gui/pages/risk.py

Risk profile page — user-facing risk settings only.

Shows only what the user needs to understand and control:
  - Daily loss limit %
  - Max losing streak (used to derive per-trade risk)
  - Calculated risk per trade (read-only formula result)
  - Equity drawdown limit %
  - Rolling window size & drawdown limit

Everything technical/internal (signal age, gateway, ports, retry counts)
belongs on the Advanced page — not here.
"""
from __future__ import annotations

import threading
import tkinter as tk
from typing import TYPE_CHECKING

import customtkinter as ctk

from src.gui.theme import (
    GREEN, RED, YELLOW, MUTED, TEXT,
    BASE, SURFACE_RAISED, LINE,
    SUCCESS_BG, SUCCESS_BORDER,
    section_rule, page_header,
)

if TYPE_CHECKING:
    from src.gui.app import ApexTraderGUI


class RiskPage(ctk.CTkScrollableFrame):
    def __init__(self, parent: tk.Widget, app: "ApexTraderGUI") -> None:
        super().__init__(parent, fg_color="transparent", corner_radius=0)
        self.app = app
        self._vars: dict[str, tk.StringVar] = {}
        self._build()
        self._load()

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        page_header(self, "Risk Profile")

        content = ctk.CTkFrame(self, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=24, pady=16)

        # ── Daily Budget ──────────────────────────────────────────────────────
        section_rule(content, "Daily Budget")

        budget_card = ctk.CTkFrame(
            content, corner_radius=8,
            fg_color=SURFACE_RAISED, border_width=1, border_color=LINE,
        )
        budget_card.pack(fill="x", pady=(0, 16))
        budget_inner = ctk.CTkFrame(budget_card, fg_color="transparent")
        budget_inner.pack(padx=24, pady=16, fill="x")

        _risk_field(
            budget_inner,
            label="Daily Loss Limit",
            key="risk.max_daily_loss_percent",
            unit="%",
            tip="Maximum percentage of your account balance the engine may lose per day.\n"
                "Example: 3 means the engine stops trading after a 3% drawdown.",
            vars_dict=self._vars,
            on_change=self._update_formula,
        )

        _risk_field(
            budget_inner,
            label="Max Losing Streak",
            key="risk.max_losing_streak",
            unit="trades",
            tip="Worst-case number of consecutive losing trades used to divide the daily budget.\n"
                "A higher streak means smaller per-trade risk.",
            vars_dict=self._vars,
            on_change=self._update_formula,
        )

        # Calculated result
        formula_row = ctk.CTkFrame(
            budget_inner, fg_color=BASE,
            corner_radius=6, border_width=1, border_color=LINE,
        )
        formula_row.pack(fill="x", pady=(10, 0))
        formula_inner = ctk.CTkFrame(formula_row, fg_color="transparent")
        formula_inner.pack(padx=16, pady=10, fill="x")

        ctk.CTkLabel(
            formula_inner,
            text="Calculated risk per trade",
            font=ctk.CTkFont(size=12), text_color=MUTED,
        ).pack(side="left")

        self._lbl_formula = ctk.CTkLabel(
            formula_inner, text="--",
            font=ctk.CTkFont(family="Consolas", size=14, weight="bold"),
            text_color=GREEN,
        )
        self._lbl_formula.pack(side="left", padx=(12, 0))

        self._lbl_formula_detail = ctk.CTkLabel(
            formula_inner, text="",
            font=ctk.CTkFont(size=11), text_color=MUTED,
        )
        self._lbl_formula_detail.pack(side="left", padx=(8, 0))

        # ── Equity Protection ─────────────────────────────────────────────────
        section_rule(content, "Equity Protection")

        equity_card = ctk.CTkFrame(
            content, corner_radius=8,
            fg_color=SURFACE_RAISED, border_width=1, border_color=LINE,
        )
        equity_card.pack(fill="x", pady=(0, 16))
        equity_inner = ctk.CTkFrame(equity_card, fg_color="transparent")
        equity_inner.pack(padx=24, pady=16, fill="x")

        _risk_field(
            equity_inner,
            label="Equity Drawdown Limit",
            key="risk.max_equity_drawdown_percent",
            unit="%",
            tip="Engine pauses all trading if live equity drops by this percentage\n"
                "from the account's starting equity for the day.",
            vars_dict=self._vars,
        )

        _risk_field(
            equity_inner,
            label="Rolling Window",
            key="risk.rolling_window_size",
            unit="trades",
            tip="Number of recent trades used to calculate rolling performance.\n"
                "Engine pauses if drawdown within this window exceeds the limit below.",
            vars_dict=self._vars,
        )

        _risk_field(
            equity_inner,
            label="Rolling Drawdown Limit",
            key="risk.rolling_drawdown_pct",
            unit="%",
            tip="Maximum drawdown allowed across the rolling window of recent trades.",
            vars_dict=self._vars,
        )

        # ── Save ──────────────────────────────────────────────────────────────
        self._lbl_status = ctk.CTkLabel(
            content, text="",
            font=ctk.CTkFont(size=12), text_color=MUTED,
        )
        self._lbl_status.pack(pady=(8, 6))

        ctk.CTkButton(
            content,
            text="💾  Save Risk Settings",
            height=44, width=240,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color=SUCCESS_BG, hover_color=SUCCESS_BORDER,
            border_width=1, border_color=SUCCESS_BORDER,
            text_color=GREEN,
            command=self._save,
        ).pack(pady=(0, 20))

    # ── Formula update ────────────────────────────────────────────────────────

    def _update_formula(self, *_) -> None:
        try:
            limit_pct = float(self._vars["risk.max_daily_loss_percent"].get())
            streak    = int(float(self._vars["risk.max_losing_streak"].get()))
            if streak < 1:
                raise ValueError
            per_trade = limit_pct / streak
            self._lbl_formula.configure(text=f"{per_trade:.2f}%", text_color=GREEN)
            self._lbl_formula_detail.configure(
                text=f"({limit_pct:.1f}% ÷ {streak} trades)",
            )
        except Exception:
            self._lbl_formula.configure(text="--", text_color=MUTED)
            self._lbl_formula_detail.configure(text="")

    # ── Load / Save ───────────────────────────────────────────────────────────

    def _load(self) -> None:
        cfg  = self.app.config.load(force=True)
        risk = cfg.get("risk", {})

        _FIELD_MAP = {
            "risk.max_daily_loss_percent":      ("max_daily_loss_percent",      "2.5"),
            "risk.max_losing_streak":           ("max_losing_streak",           "3"),
            "risk.max_equity_drawdown_percent": ("max_equity_drawdown_percent", "2.0"),
            "risk.rolling_window_size":         ("rolling_window_size",         "2"),
            "risk.rolling_drawdown_pct":        ("rolling_drawdown_pct",        "2.0"),
        }
        for key, (field, default) in _FIELD_MAP.items():
            if key in self._vars:
                v = risk.get(field)
                self._vars[key].set(str(v) if v is not None else default)

        self._update_formula()

    def _save(self) -> None:
        _WRITE_MAP = {
            "risk.max_daily_loss_percent":      ("max_daily_loss_percent",      float),
            "risk.max_losing_streak":           ("max_losing_streak",           int),
            "risk.max_equity_drawdown_percent": ("max_equity_drawdown_percent", float),
            "risk.rolling_window_size":         ("rolling_window_size",         int),
            "risk.rolling_drawdown_pct":        ("rolling_drawdown_pct",        float),
        }
        errors:  list[str]        = []
        updates: dict[str, object] = {}

        for key, (field, typ) in _WRITE_MAP.items():
            if key not in self._vars:
                continue
            raw = self._vars[key].get().strip()
            if not raw:
                continue
            try:
                updates[field] = typ(raw)
            except Exception:
                errors.append(f"'{key}' has invalid value '{raw}'")

        if errors:
            self._lbl_status.configure(
                text="⚠  " + "  |  ".join(errors), text_color=YELLOW,
            )
            return

        err = self.app.config.update("risk", updates)
        if err:
            self._lbl_status.configure(text=f"⚠  {err}", text_color=RED)
            return

        self._lbl_status.configure(
            text="✓  Saved — restarting engine…", text_color=GREEN,
        )
        self.app.app_state.mark_setup_complete(self.app.config.is_setup_complete())
        threading.Thread(target=self._delayed_restart, daemon=True).start()

    def _delayed_restart(self) -> None:
        import time
        time.sleep(0.4)
        self.app.restart_with_new_config()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _risk_field(
    parent: tk.Widget,
    label: str,
    key: str,
    unit: str,
    tip: str,
    vars_dict: dict[str, tk.StringVar],
    on_change=None,
) -> None:
    row = ctk.CTkFrame(parent, fg_color="transparent")
    row.pack(fill="x", pady=(0, 12))

    # Left column: label + tip
    left = ctk.CTkFrame(row, fg_color="transparent")
    left.pack(side="left", fill="x", expand=True)

    ctk.CTkLabel(
        left, text=label, anchor="w",
        font=ctk.CTkFont(size=13), text_color=TEXT,
    ).pack(anchor="w")

    ctk.CTkLabel(
        left, text=tip, anchor="w",
        font=ctk.CTkFont(size=11), text_color=MUTED,
        justify="left", wraplength=420,
    ).pack(anchor="w", pady=(1, 0))

    # Right column: entry + unit
    right = ctk.CTkFrame(row, fg_color="transparent")
    right.pack(side="right", padx=(16, 0))

    var = tk.StringVar()
    if on_change:
        var.trace_add("write", on_change)
    vars_dict[key] = var

    entry = ctk.CTkEntry(
        right, textvariable=var, width=80,
        font=ctk.CTkFont(family="Consolas", size=13),
        justify="center",
    )
    entry.pack(side="left")

    ctk.CTkLabel(
        right, text=unit,
        font=ctk.CTkFont(size=12), text_color=MUTED,
    ).pack(side="left", padx=(6, 0))
