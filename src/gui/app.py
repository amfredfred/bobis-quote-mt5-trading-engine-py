"""
src/gui/app.py — Apex Quant Trader desktop application.

Architecture:
  • EngineThread   — runs bootstrap/shutdown in a daemon thread
  • WSClient       — connects to UIBridge (ws://localhost:PORT) in another thread
  • GuiLogHandler  — captures Python logging into an in-process queue
  • Poll loop      — every 100 ms drains the WS queue and log queue, then
                     updates CTk widgets (all UI updates happen here on the
                     main thread so Tkinter stays happy)
"""
from __future__ import annotations

import os
import queue as _queue
import sys
import threading
from pathlib import Path

import customtkinter as ctk

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# ── Colour palette ────────────────────────────────────────────────────────────
_GREEN  = "#00d4aa"
_RED    = "#ff4757"
_YELLOW = "#ffa502"
_MUTED  = "#888888"


class ApexTraderGUI(ctk.CTk):
    """
    Main application window.

    Pass config_path to choose which config.yaml to load; default searches
    CWD → executable directory.
    """

    def __init__(self, config_path: str = "config.yaml") -> None:
        super().__init__()

        self.config_path = config_path

        # Thread-safe queue: WS messages flow from ws-client thread → here
        self._msg_queue: _queue.Queue[dict] = _queue.Queue()

        # ── Engine + WS ───────────────────────────────────────────────────────
        from src.gui.engine_thread import EngineThread
        from src.gui.ws_client import WSClient

        self.engine = EngineThread(config_path)
        self.engine.on_status_change = self._on_engine_status_change

        port = self._read_monitoring_port()
        self.ws = WSClient(
            url=f"ws://localhost:{port}",
            on_message=lambda msg: self._msg_queue.put(msg),
            on_connect=lambda: self._msg_queue.put({"type": "_ws_connected"}),
            on_disconnect=lambda: self._msg_queue.put({"type": "_ws_disconnected"}),
        )

        # ── Build UI ──────────────────────────────────────────────────────────
        self._build_window()
        self._build_header()
        self._build_error_banner()
        self._build_tabs()

        # ── Graceful close ────────────────────────────────────────────────────
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # ── Start poll loop ───────────────────────────────────────────────────
        self.after(100, self._poll)

        # ── Auto-start engine ─────────────────────────────────────────────────
        self._start_engine()

        # ── Bring window to front ─────────────────────────────────────────────
        self.lift()
        self.focus_force()

    # ── Window setup ──────────────────────────────────────────────────────────

    def _build_window(self) -> None:
        self.title("Apex Quant Trader")
        self.geometry("1140x720")
        self.minsize(920, 600)

        # Grid layout: header row + optional error banner + content row
        self.grid_rowconfigure(0, weight=0)
        self.grid_rowconfigure(1, weight=0)  # error banner (hidden by default)
        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1)

    # ── Header ────────────────────────────────────────────────────────────────

    def _build_header(self) -> None:
        hdr = ctk.CTkFrame(self, height=56, corner_radius=0, fg_color="#141428")
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.grid_propagate(False)

        # Logo
        ctk.CTkLabel(
            hdr, text="⚡  Apex Trader",
            font=ctk.CTkFont(size=17, weight="bold"),
        ).pack(side="left", padx=16)

        # Version
        version = _read_version()
        ctk.CTkLabel(
            hdr, text=f"v{version}",
            font=ctk.CTkFont(size=11), text_color=_MUTED,
        ).pack(side="left", padx=(0, 16))

        # Action buttons (right side)
        btn_frame = ctk.CTkFrame(hdr, fg_color="transparent")
        btn_frame.pack(side="right", padx=12)

        ctk.CTkButton(
            btn_frame, text="Restart", width=82, height=32,
            command=self._restart_engine,
            fg_color="#2d4a6e", hover_color="#3d5a7e",
        ).pack(side="right", padx=3)

        self._btn_stop = ctk.CTkButton(
            btn_frame, text="Stop", width=70, height=32,
            command=self._stop_engine,
            fg_color="#6e2d2d", hover_color="#8e3d3d",
        )
        self._btn_stop.pack(side="right", padx=3)

        self._btn_start = ctk.CTkButton(
            btn_frame, text="Start", width=70, height=32,
            command=self._start_engine,
            fg_color="#1a5c2a", hover_color="#22732e",
        )
        self._btn_start.pack(side="right", padx=3)

        # Status indicators (centre-right)
        status_frame = ctk.CTkFrame(hdr, fg_color="transparent")
        status_frame.pack(side="right", padx=20)

        self._lbl_engine  = _StatusDot(status_frame, "Engine",  "gray")
        self._lbl_mt5     = _StatusDot(status_frame, "MT5",     "gray")
        self._lbl_gateway = _StatusDot(status_frame, "Gateway", "gray")

    # ── Tabs ──────────────────────────────────────────────────────────────────

    def _build_error_banner(self) -> None:
        """Collapsible red banner shown when engine is in error state."""
        self._error_banner = ctk.CTkFrame(
            self, fg_color="#3a1010", corner_radius=0, height=38,
        )
        # Not gridded initially — shown on error
        self._error_banner.grid_propagate(False)

        self._error_icon = ctk.CTkLabel(
            self._error_banner, text="⚠",
            font=ctk.CTkFont(size=14), text_color=_RED,
        )
        self._error_icon.pack(side="left", padx=(12, 4))

        self._error_lbl = ctk.CTkLabel(
            self._error_banner, text="",
            font=ctk.CTkFont(size=12), text_color="#ffaaaa",
            anchor="w",
        )
        self._error_lbl.pack(side="left", fill="x", expand=True)

        ctk.CTkButton(
            self._error_banner, text="Open Settings →", width=130, height=26,
            command=lambda: self._tabs.set("Settings"),
            fg_color="#6e1a1a", hover_color="#8e2a2a",
        ).pack(side="right", padx=8)

        ctk.CTkButton(
            self._error_banner, text="Retry", width=70, height=26,
            command=self._restart_engine,
            fg_color="#2d4a6e", hover_color="#3d5a7e",
        ).pack(side="right", padx=4)

    def _show_error_banner(self, msg: str) -> None:
        self._error_lbl.configure(text=f"Engine error: {msg}")
        self._error_banner.grid(row=1, column=0, sticky="ew")

    def _hide_error_banner(self) -> None:
        self._error_banner.grid_remove()

    def _build_tabs(self) -> None:
        tabs = ctk.CTkTabview(self, corner_radius=8)
        tabs.grid(row=2, column=0, sticky="nsew", padx=8, pady=(4, 8))
        self._tabs = tabs

        tabs.add("Dashboard")
        tabs.add("Settings")
        tabs.add("Logs")

        from src.gui.tabs.dashboard import DashboardTab
        from src.gui.tabs.settings  import SettingsTab
        from src.gui.tabs.logs      import LogsTab

        self._tab_dashboard = DashboardTab(tabs.tab("Dashboard"), self)
        self._tab_settings  = SettingsTab(tabs.tab("Settings"),   self)
        self._tab_logs      = LogsTab(tabs.tab("Logs"),           self)

    # ── Engine lifecycle ──────────────────────────────────────────────────────

    def _start_engine(self) -> None:
        if not self.engine.is_running():
            self.engine.start()
            self.ws.start()
        self._lbl_engine.set("Starting…", _YELLOW)

    def _stop_engine(self) -> None:
        def _do() -> None:
            self.ws.stop()
            self.engine.stop()
        threading.Thread(target=_do, daemon=True).start()

    def _restart_engine(self) -> None:
        def _do() -> None:
            self.ws.stop()
            self.engine.stop()
            import time; time.sleep(0.8)
            # Reload monitoring port in case it changed
            port = self._read_monitoring_port()
            self.ws.url = f"ws://localhost:{port}"
            self.engine.start()
            self.ws.start()
        threading.Thread(target=_do, daemon=True).start()
        self._lbl_engine.set("Restarting…", _YELLOW)

    def restart_with_new_config(self) -> None:
        """Called by SettingsTab after writing config.yaml."""
        self._restart_engine()

    # ── Status update (called from engine thread via on_status_change) ────────

    def _on_engine_status_change(self, status: str, error: str | None) -> None:
        self.after(0, lambda: self._apply_engine_status(status, error))

    def _apply_engine_status(self, status: str, error: str | None) -> None:
        colours = {
            "stopped":  "gray",
            "starting": _YELLOW,
            "running":  _GREEN,
            "stopping": _YELLOW,
            "error":    _RED,
        }
        labels = {
            "stopped":  "Stopped",
            "starting": "Starting…",
            "running":  "Running",
            "stopping": "Stopping…",
            "error":    "Error",
        }
        text  = labels.get(status, status)
        color = colours.get(status, "gray")
        if error:
            text = f"Error"
        self._lbl_engine.set(text, color)

        if status == "error" and error:
            self._show_error_banner(error)
            # Switch to Settings so user sees how to fix it
            try:
                self._tabs.set("Settings")
            except Exception:
                pass
        elif status == "running":
            self._hide_error_banner()
            try:
                self._tabs.set("Dashboard")
            except Exception:
                pass

    # ── Poll loop — runs on main thread every 100 ms ──────────────────────────

    def _poll(self) -> None:
        # Drain WS message queue
        try:
            while True:
                msg = self._msg_queue.get_nowait()
                self._dispatch(msg)
        except _queue.Empty:
            pass

        # Drain in-process log buffer
        from src.gui.log_relay import drain
        entries = drain()
        if entries:
            self._tab_logs.append_logs_bulk(entries)

        self.after(100, self._poll)

    def _dispatch(self, msg: dict) -> None:
        t       = msg.get("type", "")
        payload = msg.get("payload", {})

        if t == "_ws_connected":
            self._lbl_gateway.set("Bridge OK", _GREEN)

        elif t == "_ws_disconnected":
            self._lbl_gateway.set("Bridge --", "gray")

        elif t == "STATE_SNAPSHOT":
            self._tab_dashboard.on_snapshot(payload)
            engine_info  = payload.get("engine", {})
            mt5_ok       = payload.get("connected", False)
            gw_ok        = engine_info.get("connected_signal_engine", False)
            self._lbl_mt5.set(
                "Connected" if mt5_ok else "Disconnected",
                _GREEN if mt5_ok else _RED,
            )
            self._lbl_gateway.set(
                "Active" if gw_ok else "Connecting…",
                _GREEN if gw_ok else _YELLOW,
            )

        elif t == "METRICS_UPDATE":
            self._tab_dashboard.on_metrics(payload)

        elif t in (
            "trade.opened", "trade.tp1_hit",
            "trade.tp2_hit", "trade.sl_hit", "trade.closed",
        ):
            self._tab_dashboard.on_trade_event(t, payload)

        elif t in (
            "signal.received", "signal.triggered",
            "risk.approved", "risk.rejected",
        ):
            self._tab_dashboard.on_signal_event(t, payload)

        elif t == "engine.paused":
            pass  # dashboard handles its own button state

        elif t == "engine.resumed":
            pass

    # ── WS command helper (public) ────────────────────────────────────────────

    def send_command(self, cmd_type: str, payload: dict | None = None) -> None:
        self.ws.send(cmd_type, payload or {})

    # ── Close handler ─────────────────────────────────────────────────────────

    def _on_close(self) -> None:
        self._lbl_engine.set("Shutting down…", _YELLOW)
        self.update_idletasks()

        def _do() -> None:
            self.ws.stop()
            self.engine.stop(timeout=10)
            self.after(0, self.destroy)

        threading.Thread(target=_do, daemon=True).start()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _read_monitoring_port(self) -> int:
        try:
            import yaml
            with open(self.config_path, "r", encoding="utf-8") as fh:
                cfg = yaml.safe_load(fh) or {}
            return int(cfg.get("engine", {}).get("monitoring_port", 8080))
        except Exception:
            return 8080


# ── Status dot widget ─────────────────────────────────────────────────────────

class _StatusDot(ctk.CTkLabel):
    """Coloured dot + text status indicator."""

    def __init__(self, parent: ctk.CTkBaseClass, name: str, color: str) -> None:
        super().__init__(
            parent,
            text=f"●  {name}: --",
            font=ctk.CTkFont(size=12),
            text_color=color,
        )
        self.pack(side="left", padx=10)
        self._name = name

    def set(self, status: str, color: str) -> None:
        self.configure(text=f"●  {self._name}: {status}", text_color=color)


# ── Version helper ────────────────────────────────────────────────────────────

def _read_version() -> str:
    for candidate in (
        Path("version.txt"),
        Path(sys.executable).parent / "version.txt",
        Path(__file__).parent.parent.parent / "version.txt",
    ):
        try:
            return candidate.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    return "?"


# ── Resolve config path (exported for __main__) ───────────────────────────────

def resolve_config_path(argv: list[str]) -> str:
    """
    Search order:
      1. First non-flag CLI arg that ends with .yaml
      2. config.yaml in CWD
      3. config.yaml next to the executable (PyInstaller layout)
    """
    for arg in argv[1:]:
        if not arg.startswith("-") and arg.endswith(".yaml"):
            return arg

    if os.path.exists("config.yaml"):
        return "config.yaml"

    exe_cfg = Path(sys.executable).parent / "config.yaml"
    if exe_cfg.exists():
        return str(exe_cfg)

    return "config.yaml"  # will surface a friendly error in SettingsTab
