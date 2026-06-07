"""
src/gui/tabs/dashboard.py — Dashboard tab.

Shows:
  • Metric cards   — balance, equity, daily P&L, drawdown, open trades
  • Positions table — live open trades with close button
  • Risk guards    — daily loss / equity DD / rolling window status
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING

import customtkinter as ctk

if TYPE_CHECKING:
    from src.gui.app import ApexTraderGUI

# ── Colour helpers ────────────────────────────────────────────────────────────

_GREEN  = "#00d4aa"
_RED    = "#ff4757"
_YELLOW = "#ffa502"
_MUTED  = "#888888"
_TEXT   = "#e0e0e0"

_SIDE_COLOURS = {"BUY": _GREEN, "buy": _GREEN, "SELL": _RED, "sell": _RED}


class DashboardTab(ctk.CTkFrame):
    def __init__(self, parent: tk.Widget, app: "ApexTraderGUI") -> None:
        super().__init__(parent, fg_color="transparent")
        self.pack(fill="both", expand=True)
        self.app = app
        self._trades: dict[str, str] = {}  # trade_id → treeview item id
        self._build()

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        # ── Metric cards ──────────────────────────────────────────────────────
        cards_row = ctk.CTkFrame(self, fg_color="transparent")
        cards_row.pack(fill="x", padx=10, pady=(10, 6))

        self._card_balance  = self._make_card(cards_row, "Balance",      "--")
        self._card_equity   = self._make_card(cards_row, "Equity",       "--")
        self._card_pnl      = self._make_card(cards_row, "Daily P&L",    "--")
        self._card_drawdown = self._make_card(cards_row, "Drawdown",     "--")
        self._card_open     = self._make_card(cards_row, "Open Trades",  "--")

        # ── Positions section ─────────────────────────────────────────────────
        pos_frame = ctk.CTkFrame(self, corner_radius=8)
        pos_frame.pack(fill="both", expand=True, padx=10, pady=4)

        header_row = ctk.CTkFrame(pos_frame, fg_color="transparent")
        header_row.pack(fill="x", padx=10, pady=(8, 4))
        ctk.CTkLabel(
            header_row, text="Open Positions",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(side="left")

        # Action buttons
        btn_row = ctk.CTkFrame(header_row, fg_color="transparent")
        btn_row.pack(side="right")
        ctk.CTkButton(
            btn_row, text="Close Selected", width=120, height=28,
            command=self._close_selected, fg_color="#6e2d2d", hover_color="#8e3d3d",
        ).pack(side="left", padx=4)
        self._btn_pause = ctk.CTkButton(
            btn_row, text="Pause", width=80, height=28,
            command=self._toggle_pause, fg_color="#4a3a0a", hover_color="#6a5a1a",
        )
        self._btn_pause.pack(side="left", padx=4)
        self._paused = False

        # ttk Treeview
        tree_wrap = ctk.CTkFrame(pos_frame, fg_color="#1a1a2a", corner_radius=6)
        tree_wrap.pack(fill="both", expand=True, padx=8, pady=(0, 6))

        self._style_treeview()

        cols = ("id", "symbol", "side", "entry", "sl", "tp1", "tp2", "lots", "state")
        self._tree = ttk.Treeview(
            tree_wrap, columns=cols, show="headings",
            style="Dashboard.Treeview", height=7,
        )
        col_cfg = {
            "id":     ("Trade ID",  90), "symbol": ("Symbol", 70),
            "side":   ("Side",      50), "entry":  ("Entry",  80),
            "sl":     ("SL",        80), "tp1":    ("TP1",    80),
            "tp2":    ("TP2",       80), "lots":   ("Lots",   55),
            "state":  ("State",     75),
        }
        for col, (label, width) in col_cfg.items():
            self._tree.heading(col, text=label)
            self._tree.column(col, width=width, anchor="center", minwidth=50)

        vsb = ttk.Scrollbar(tree_wrap, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)
        self._tree.pack(side="left", fill="both", expand=True, padx=(4, 0), pady=4)
        vsb.pack(side="right", fill="y", pady=4)

        # Colour BUY / SELL rows
        self._tree.tag_configure("BUY",  foreground=_GREEN)
        self._tree.tag_configure("SELL", foreground=_RED)

        # ── Risk guards ───────────────────────────────────────────────────────
        guards_row = ctk.CTkFrame(self, fg_color="transparent")
        guards_row.pack(fill="x", padx=10, pady=(2, 8))

        ctk.CTkLabel(
            guards_row, text="Risk Guards:",
            font=ctk.CTkFont(size=11, weight="bold"), text_color=_MUTED,
        ).pack(side="left", padx=(4, 8))

        self._lbl_guard: list[ctk.CTkLabel] = []
        for _ in range(3):
            lbl = ctk.CTkLabel(
                guards_row, text="--", text_color=_MUTED,
                font=ctk.CTkFont(size=11),
            )
            lbl.pack(side="left", padx=10)
            self._lbl_guard.append(lbl)

    # ── Treeview style (dark theme) ───────────────────────────────────────────

    @staticmethod
    def _style_treeview() -> None:
        s = ttk.Style()
        try:
            s.theme_use("clam")
        except Exception:
            pass
        s.configure(
            "Dashboard.Treeview",
            background="#1a1a2a", foreground=_TEXT,
            fieldbackground="#1a1a2a", rowheight=26,
            font=("Consolas", 11),
        )
        s.configure(
            "Dashboard.Treeview.Heading",
            background="#2a2a3e", foreground="#9999cc",
            font=("Consolas", 11, "bold"), relief="flat",
        )
        s.map("Dashboard.Treeview", background=[("selected", "#2d4a6e")])

    # ── Card helper ───────────────────────────────────────────────────────────

    @staticmethod
    def _make_card(parent: tk.Widget, title: str, value: str) -> ctk.CTkLabel:
        card = ctk.CTkFrame(parent, corner_radius=8)
        card.pack(side="left", fill="both", expand=True, padx=4)
        ctk.CTkLabel(
            card, text=title,
            font=ctk.CTkFont(size=11), text_color=_MUTED,
        ).pack(pady=(8, 0))
        lbl = ctk.CTkLabel(
            card, text=value,
            font=ctk.CTkFont(size=18, weight="bold"), text_color=_TEXT,
        )
        lbl.pack(pady=(2, 8))
        return lbl

    # ── UIBridge message handlers (called on GUI main thread) ─────────────────

    def on_snapshot(self, snap: dict) -> None:
        metrics = snap.get("metrics", {})
        self._update_metrics(metrics)

        self._tree.delete(*self._tree.get_children())
        self._trades.clear()
        for trade in snap.get("trades", []):
            self._insert_trade(trade)

        for guard in snap.get("riskGuards", []):
            self._update_guard(guard)

        engine = snap.get("engine", {})
        self._paused = bool(engine.get("is_paused", False))
        self._btn_pause.configure(
            text="Resume" if self._paused else "Pause",
            fg_color="#0a4a1a" if self._paused else "#4a3a0a",
        )

    def on_metrics(self, m: dict) -> None:
        self._update_metrics(m)

    def on_trade_event(self, event_type: str, payload: dict) -> None:
        if event_type == "trade.opened":
            self._insert_trade(payload)
        elif event_type in ("trade.tp2_hit", "trade.sl_hit", "trade.closed"):
            tid = payload.get("trade_id", "")
            if tid in self._trades:
                try:
                    self._tree.delete(self._trades.pop(tid))
                except Exception:
                    pass
        elif event_type == "trade.tp1_hit":
            tid = payload.get("trade_id", "")
            if tid in self._trades:
                self._tree.item(self._trades[tid], tags=("tp1",))
                self._tree.tag_configure("tp1", foreground=_YELLOW)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _insert_trade(self, t: dict) -> None:
        tid = t.get("id", "")
        side = str(t.get("side", "")).upper()

        def _fmt(v) -> str:
            try:
                return f"{float(v):.4f}"
            except Exception:
                return str(v) if v else "--"

        item = self._tree.insert(
            "", "end",
            values=(
                tid,
                t.get("symbol", ""),
                side,
                _fmt(t.get("entry_price")),
                _fmt(t.get("sl")),
                _fmt(t.get("tp1")),
                _fmt(t.get("tp2")),
                t.get("lots", ""),
                t.get("state", ""),
            ),
            tags=(side,),
        )
        if tid:
            self._trades[tid] = item

    def _update_metrics(self, m: dict) -> None:
        currency = m.get("currency", "")
        bal      = m.get("balance", m.get("current_balance"))
        equity   = m.get("equity")
        pnl      = m.get("daily_pnl")
        dd       = m.get("drawdown_pct")
        opens    = m.get("open_trades")

        def _money(v) -> str:
            if v is None:
                return "--"
            prefix = f"{currency} " if currency else ""
            return f"{prefix}{v:,.2f}"

        self._card_balance.configure(text=_money(bal))
        self._card_equity.configure(text=_money(equity))

        if pnl is not None:
            sign  = "+" if pnl >= 0 else ""
            color = _GREEN if pnl >= 0 else _RED
            self._card_pnl.configure(text=f"{sign}{pnl:,.2f}", text_color=color)
        else:
            self._card_pnl.configure(text="--", text_color=_TEXT)

        if dd is not None:
            color = _RED if dd > 1.5 else _YELLOW if dd > 0.5 else _TEXT
            self._card_drawdown.configure(text=f"{dd:.2f}%", text_color=color)
        else:
            self._card_drawdown.configure(text="--", text_color=_TEXT)

        self._card_open.configure(text=str(opens) if opens is not None else "--")

    def _update_guard(self, guard: dict) -> None:
        idx_map = {"guard1": 0, "guard2": 1, "guard3": 2}
        idx = idx_map.get(guard.get("id", ""), -1)
        if idx < 0:
            return
        status    = guard.get("status", "ACTIVE")
        current   = guard.get("current_value", 0.0)
        threshold = guard.get("threshold", 0.0)
        unit      = guard.get("unit", "")
        name      = guard.get("name", "")

        if status == "PAUSED":
            color = _RED
        elif status == "DISABLED":
            color = _MUTED
        elif threshold and current >= threshold * 0.75:
            color = _YELLOW
        else:
            color = _GREEN

        self._lbl_guard[idx].configure(
            text=f"{name}: {current:.2f}/{threshold}{unit}", text_color=color
        )

    def _close_selected(self) -> None:
        sel = self._tree.selection()
        if not sel:
            return
        trade_id = self._tree.item(sel[0], "values")[0]
        self.app.send_command("cmd.close_trade", {"trade_id": trade_id})

    def _toggle_pause(self) -> None:
        if self._paused:
            self.app.send_command("cmd.resume", {})
            self._paused = False
            self._btn_pause.configure(text="Pause", fg_color="#4a3a0a")
        else:
            self.app.send_command("cmd.pause", {})
            self._paused = True
            self._btn_pause.configure(text="Resume", fg_color="#0a4a1a")
