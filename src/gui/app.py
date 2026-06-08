"""
src/gui/app.py — Apex Quant Trader Engine Manager

Sidebar-based desktop control panel.

Layout
──────
  Left sidebar (fixed 190 px)
    ⚡ Apex Trader
    v{version}
    ──────────────
    ⬡  Overview         ← home / live trading view
    ⚡  Engine           ← process control
    ⬡  Terminal         ← MT5 selection + credentials
    ⚖  Risk             ← user risk settings
    📋  Logs             ← log viewer
    ⚙  Advanced         ← technical / developer settings
    ──────────────
    ●  Engine: {status}

  Right content area
    Page frames — shown/hidden by nav

Architecture
────────────
  ServiceController  owns engine lifecycle (Windows service via sc.exe)
  WSClient           connects to UIBridge (ws://localhost:{port})
  Pages              receive events via on_*() methods; call app.svc / app.ws
"""
from __future__ import annotations

import queue as _queue
import sys
import threading
import time
from pathlib import Path
from typing import Any

import customtkinter as ctk
import yaml

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

from src.gui.theme import (  # noqa: E402  (after CTk setup)
    GREEN, RED, YELLOW, MUTED,
    TEXT, BASE, SURFACE, SURFACE_RAISED,
    LINE, LINE_STRONG,
    NAV_HOVER, NAV_ACTIVE_BG,
)

_NAV_PAGES = ["Overview", "Engine", "Terminal", "Risk", "Logs", "Advanced"]
_NAV_ICONS = {
    "Overview":  "⬡",
    "Engine":    "⚡",
    "Terminal":  "⬡",
    "Risk":      "⚖",
    "Logs":      "📋",
    "Advanced":  "⚙",
}


# ── Main application window ───────────────────────────────────────────────────

class ApexTraderGUI(ctk.CTk):
    """
    Root window.  Owns the service controller, WebSocket client, and
    all page frames.  Pages call back through:
        self.app.svc.*             — service control
        self.app.ws.send(...)      — send commands to engine
        self.app.load_config()     — read config.yaml
        self.app.save_config(cfg)  — write config.yaml
        self.app.navigate(page)    — switch sidebar page
        self.app._last_heartbeat   — float timestamp of last WS message
    """

    def __init__(self, config_path: str = "config.yaml") -> None:
        super().__init__()

        self.config_path       = config_path
        self._msg_queue:        _queue.Queue[dict] = _queue.Queue()
        self._last_heartbeat:   float = 0.0
        self._prev_svc_status:  str   = ""

        from src.gui.service_controller import ServiceController
        from src.gui.ws_client import WSClient

        self.svc = ServiceController()
        self.svc.on_status_change = self._on_svc_status_change

        port = self._read_monitoring_port()
        self.ws = WSClient(
            url=f"ws://localhost:{port}",
            on_message=lambda msg: self._msg_queue.put(msg),
            on_connect=lambda: self._msg_queue.put({"type": "_ws_connected"}),
            on_disconnect=lambda: self._msg_queue.put({"type": "_ws_disconnected"}),
        )

        self._build_layout()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._poll)

        self.ws.start()
        self.after(300, self._refresh_service_status)

        self.lift()
        self.focus_force()

    # ── Window & layout ───────────────────────────────────────────────────────

    def _build_layout(self) -> None:
        self.title("Apex Quant Trader")
        self.geometry("1180x740")
        self.minsize(980, 620)

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)

        # Sidebar
        self._sidebar = ctk.CTkFrame(
            self, width=190, corner_radius=0, fg_color=BASE,
        )
        self._sidebar.grid(row=0, column=0, sticky="nsew")
        self._sidebar.grid_propagate(False)

        # Content area
        self._content = ctk.CTkFrame(self, corner_radius=0, fg_color=SURFACE)
        self._content.grid(row=0, column=1, sticky="nsew")
        self._content.grid_rowconfigure(0, weight=1)
        self._content.grid_columnconfigure(0, weight=1)

        self._build_sidebar()
        self._build_pages()
        self._show_page("Overview")

    def _build_sidebar(self) -> None:
        # Logo block
        logo = ctk.CTkFrame(self._sidebar, fg_color="transparent", height=62)
        logo.pack(fill="x")
        logo.pack_propagate(False)
        ctk.CTkLabel(
            logo,
            text="⚡  Apex Trader",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=GREEN,
        ).place(relx=0.5, rely=0.58, anchor="center")

        # Version
        version = _read_version()
        ctk.CTkLabel(
            self._sidebar,
            text=f"v{version}",
            font=ctk.CTkFont(size=10),
            text_color=MUTED,
        ).pack(pady=(0, 6))

        _divider(self._sidebar)

        # Nav buttons
        self._nav_btns: dict[str, ctk.CTkButton] = {}
        for page in _NAV_PAGES:
            icon = _NAV_ICONS.get(page, "")
            btn = ctk.CTkButton(
                self._sidebar,
                text=f"  {icon}  {page}",
                anchor="w",
                height=38, corner_radius=6,
                fg_color="transparent",
                hover_color=NAV_HOVER,
                text_color=MUTED,
                font=ctk.CTkFont(size=13),
                command=lambda p=page: self._show_page(p),
            )
            btn.pack(fill="x", padx=8, pady=2)
            self._nav_btns[page] = btn

        # Spacer pushes status to bottom
        ctk.CTkFrame(self._sidebar, fg_color="transparent").pack(
            fill="both", expand=True,
        )

        # Engine status at bottom of sidebar
        _divider(self._sidebar)
        self._sidebar_engine_status = ctk.CTkLabel(
            self._sidebar,
            text="●  Engine: --",
            font=ctk.CTkFont(size=11),
            text_color=MUTED,
        )
        self._sidebar_engine_status.pack(pady=(4, 14))

    def _build_pages(self) -> None:
        from src.gui.pages.overview  import OverviewPage
        from src.gui.pages.engine    import EnginePage
        from src.gui.pages.terminal  import TerminalPage
        from src.gui.pages.risk      import RiskPage
        from src.gui.pages.logs      import LogsPage
        from src.gui.pages.advanced  import AdvancedPage

        self._pages: dict[str, ctk.CTkFrame] = {
            "Overview":  OverviewPage(self._content, self),
            "Engine":    EnginePage(self._content, self),
            "Terminal":  TerminalPage(self._content, self),
            "Risk":      RiskPage(self._content, self),
            "Logs":      LogsPage(self._content, self),
            "Advanced":  AdvancedPage(self._content, self),
        }

    # ── Navigation ────────────────────────────────────────────────────────────

    def _show_page(self, name: str) -> None:
        for pname, page in self._pages.items():
            if pname == name:
                page.grid(row=0, column=0, sticky="nsew")
            else:
                page.grid_remove()

        for bname, btn in self._nav_btns.items():
            active = bname == name
            btn.configure(
                fg_color=NAV_ACTIVE_BG if active else "transparent",
                text_color=GREEN if active else MUTED,
                border_width=1 if active else 0,
                border_color=LINE_STRONG if active else BASE,
            )

    def navigate(self, page: str) -> None:
        """Called by pages to navigate programmatically."""
        self._show_page(page)

    # ── Config helpers ────────────────────────────────────────────────────────

    def load_config(self) -> dict:
        try:
            with open(self.config_path, "r", encoding="utf-8") as fh:
                return yaml.safe_load(fh) or {}
        except Exception:
            return {}

    def save_config(self, cfg: dict) -> None:
        with open(self.config_path, "w", encoding="utf-8") as fh:
            yaml.dump(
                cfg, fh,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )

    def restart_with_new_config(self) -> None:
        """Called by settings pages after saving config.yaml."""
        self.svc.restart()

    # ── WS command helper ─────────────────────────────────────────────────────

    def send_command(self, cmd_type: str, payload: dict | None = None) -> None:
        self.ws.send(cmd_type, payload or {})

    # ── Service status ────────────────────────────────────────────────────────

    def _on_svc_status_change(self, status: str, detail: str | None) -> None:
        self.after(0, lambda: self._apply_svc_status(status, detail))

    def _apply_svc_status(self, status: str, detail: str | None) -> None:
        from src.gui.service_controller import ServiceStatus

        _colours = {
            ServiceStatus.NOT_INSTALLED: MUTED,
            ServiceStatus.STOPPED:       RED,
            ServiceStatus.STARTING:      YELLOW,
            ServiceStatus.RUNNING:       GREEN,
            ServiceStatus.STOPPING:      YELLOW,
            ServiceStatus.UNKNOWN:       MUTED,
        }
        _labels = {
            ServiceStatus.NOT_INSTALLED: "Not Installed",
            ServiceStatus.STOPPED:       "Stopped",
            ServiceStatus.STARTING:      "Starting…",
            ServiceStatus.RUNNING:       "Running",
            ServiceStatus.STOPPING:      "Stopping…",
            ServiceStatus.UNKNOWN:       "Unknown",
        }
        label = _labels.get(status, status)
        color = _colours.get(status, MUTED)

        self._sidebar_engine_status.configure(
            text=f"●  Engine: {label}", text_color=color,
        )

        # Crash detection: RUNNING → STOPPED without an explicit stop
        if (
            self._prev_svc_status == ServiceStatus.RUNNING
            and status == ServiceStatus.STOPPED
            and not detail
        ):
            detail = "Engine stopped unexpectedly. Click Start to restart it."

        self._prev_svc_status = status

        # Broadcast to all pages
        _broadcast(self._pages, "on_engine_status", status, detail)

    def _refresh_service_status(self) -> None:
        """Poll service status every 5 s."""
        def _check():
            status = self.svc.query()
            self.after(0, lambda: self._apply_svc_status(status, None))

        threading.Thread(target=_check, daemon=True).start()
        self.after(5000, self._refresh_service_status)

    # ── WebSocket message dispatch ────────────────────────────────────────────

    def _poll(self) -> None:
        try:
            while True:
                msg = self._msg_queue.get_nowait()
                self._dispatch(msg)
        except _queue.Empty:
            pass
        self.after(100, self._poll)

    def _dispatch(self, msg: dict) -> None:
        t       = msg.get("type", "")
        payload = msg.get("payload", {})

        if t == "_ws_connected":
            self._last_heartbeat = time.time()
            _broadcast(self._pages, "on_ws_connected")

        elif t == "_ws_disconnected":
            _broadcast(self._pages, "on_ws_disconnected")

        elif t == "STATE_SNAPSHOT":
            self._last_heartbeat = time.time()
            _broadcast(self._pages, "on_snapshot", payload)

        elif t == "METRICS_UPDATE":
            self._last_heartbeat = time.time()
            _broadcast(self._pages, "on_metrics", payload)

        elif t == "mt5.error":
            msg_text = payload.get("message", "MT5 connection failed")
            _broadcast(self._pages, "on_mt5_error", msg_text)

        elif t in (
            "trade.opened", "trade.tp1_hit",
            "trade.tp2_hit", "trade.sl_hit", "trade.closed",
        ):
            _broadcast(self._pages, "on_trade_event", t, payload)

        elif t in (
            "signal.received", "signal.triggered",
            "risk.approved",   "risk.rejected",
        ):
            _broadcast(self._pages, "on_signal_event", t, payload)

    # ── Close ─────────────────────────────────────────────────────────────────

    def _on_close(self) -> None:
        self.ws.stop()
        self.destroy()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _read_monitoring_port(self) -> int:
        try:
            with open(self.config_path, "r", encoding="utf-8") as fh:
                cfg = yaml.safe_load(fh) or {}
            return int(cfg.get("engine", {}).get("monitoring_port", 8080))
        except Exception:
            return 8080


# ── Module-level helpers ──────────────────────────────────────────────────────

def _broadcast(
    pages: dict[str, ctk.CTkFrame],
    method: str,
    *args: Any,
) -> None:
    """Call method(*args) on every page that implements it, swallowing errors."""
    for page in pages.values():
        cb = getattr(page, method, None)
        if callable(cb):
            try:
                cb(*args)
            except Exception:
                pass


def _divider(parent: ctk.CTkFrame) -> None:
    ctk.CTkFrame(parent, height=1, fg_color=LINE).pack(
        fill="x", padx=12, pady=6,
    )


def _read_version() -> str:
    for candidate in (
        Path("version.txt"),
        Path(sys.executable).parent / "version.txt",
        Path(__file__).resolve().parent.parent.parent / "version.txt",
    ):
        try:
            return candidate.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    return "?"


def resolve_config_path(argv: list[str]) -> str:
    """
    Locate config.yaml in priority order:

    1. Explicit CLI argument ending in .yaml
    2. Next to the exe (user-editable installed copy)
    3. Walk up 3 levels from the exe — covers dev/dist layouts
    4. sys._MEIPASS (PyInstaller _internal/ bundle directory)
    5. CWD
    6. Walk up from __file__ (editable venv install)
    7. Fallback "config.yaml"
    """
    for arg in argv[1:]:
        if not arg.startswith("-") and arg.endswith(".yaml"):
            return arg

    exe_dir = Path(sys.executable).parent
    for depth in range(4):
        candidate = exe_dir
        for _ in range(depth):
            candidate = candidate.parent
        cfg = candidate / "config.yaml"
        if cfg.exists():
            return str(cfg)

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bundled = Path(meipass) / "config.yaml"
        if bundled.exists():
            return str(bundled)

    cwd_cfg = Path.cwd() / "config.yaml"
    if cwd_cfg.exists():
        return str(cwd_cfg)

    for parent in Path(__file__).resolve().parents:
        cfg = parent / "config.yaml"
        if cfg.exists():
            return str(cfg)

    return "config.yaml"
