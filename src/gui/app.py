"""
src/gui/app.py — Apex Quant Trader desktop control panel.

Architecture (correct):
  • The ENGINE runs as a Windows service (apex-quant-trader-agent) 24/7.
    It connects to the cloud gateway so the online dashboard always works,
    regardless of whether this GUI is open or closed.

  • THIS APP is a service controller + live monitor:
      - ServiceController  — start / stop / restart via sc.exe
      - WSClient           — connects to UIBridge (ws://localhost:8080)
                             for live trades, metrics and logs
      - Poll loop          — drains WS queue every 100 ms → updates CTk widgets

  Closing this window does NOT stop the engine service.
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

_GREEN  = "#00d4aa"
_RED    = "#ff4757"
_YELLOW = "#ffa502"
_MUTED  = "#888888"


class ApexTraderGUI(ctk.CTk):
    """
    Apex Quant Trader desktop control panel.

    Opens config.yaml from config_path (auto-resolved if omitted).
    """

    def __init__(self, config_path: str = "config.yaml") -> None:
        super().__init__()

        self.config_path = config_path
        self._msg_queue: _queue.Queue[dict] = _queue.Queue()

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

        self._build_window()
        self._build_header()
        self._build_error_banner()
        self._build_tabs()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._poll)

        # Connect to UIBridge (engine may already be running as service)
        self.ws.start()

        # Refresh service status indicator on startup
        self.after(300, self._refresh_service_status)

        self.lift()
        self.focus_force()

    # ── Window ────────────────────────────────────────────────────────────────

    def _build_window(self) -> None:
        self.title("Apex Quant Trader")
        self.geometry("1140x720")
        self.minsize(920, 600)
        self.grid_rowconfigure(0, weight=0)
        self.grid_rowconfigure(1, weight=0)
        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1)

    # ── Header ────────────────────────────────────────────────────────────────

    def _build_header(self) -> None:
        hdr = ctk.CTkFrame(self, height=56, corner_radius=0, fg_color="#141428")
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.grid_propagate(False)

        ctk.CTkLabel(
            hdr, text="⚡  Apex Trader",
            font=ctk.CTkFont(size=17, weight="bold"),
        ).pack(side="left", padx=16)

        version = _read_version()
        ctk.CTkLabel(
            hdr, text=f"v{version}",
            font=ctk.CTkFont(size=11), text_color=_MUTED,
        ).pack(side="left", padx=(0, 16))

        # Service control buttons
        btn_frame = ctk.CTkFrame(hdr, fg_color="transparent")
        btn_frame.pack(side="right", padx=12)

        ctk.CTkButton(
            btn_frame, text="Restart", width=82, height=32,
            command=self._restart_service,
            fg_color="#2d4a6e", hover_color="#3d5a7e",
        ).pack(side="right", padx=3)

        self._btn_stop = ctk.CTkButton(
            btn_frame, text="Stop", width=70, height=32,
            command=self._stop_service,
            fg_color="#6e2d2d", hover_color="#8e3d3d",
        )
        self._btn_stop.pack(side="right", padx=3)

        self._btn_start = ctk.CTkButton(
            btn_frame, text="Start", width=70, height=32,
            command=self._start_service,
            fg_color="#1a5c2a", hover_color="#22732e",
        )
        self._btn_start.pack(side="right", padx=3)

        # Status indicators
        status_frame = ctk.CTkFrame(hdr, fg_color="transparent")
        status_frame.pack(side="right", padx=20)

        self._lbl_svc     = _StatusDot(status_frame, "Service", "gray")
        self._lbl_mt5     = _StatusDot(status_frame, "MT5",     "gray")
        self._lbl_gateway = _StatusDot(status_frame, "Gateway", "gray")

    # ── Error banner ──────────────────────────────────────────────────────────

    def _build_error_banner(self) -> None:
        self._error_banner = ctk.CTkFrame(
            self, fg_color="#3a1010", corner_radius=0, height=38,
        )
        self._error_banner.grid_propagate(False)

        ctk.CTkLabel(
            self._error_banner, text="⚠",
            font=ctk.CTkFont(size=14), text_color=_RED,
        ).pack(side="left", padx=(12, 4))

        self._error_lbl = ctk.CTkLabel(
            self._error_banner, text="",
            font=ctk.CTkFont(size=12), text_color="#ffaaaa", anchor="w",
        )
        self._error_lbl.pack(side="left", fill="x", expand=True)

        ctk.CTkButton(
            self._error_banner, text="Open Settings →", width=130, height=26,
            command=lambda: self._tabs.set("Settings"),
            fg_color="#6e1a1a", hover_color="#8e2a2a",
        ).pack(side="right", padx=8)

        ctk.CTkButton(
            self._error_banner, text="Restart", width=70, height=26,
            command=self._restart_service,
            fg_color="#2d4a6e", hover_color="#3d5a7e",
        ).pack(side="right", padx=4)

        self._btn_install = ctk.CTkButton(
            self._error_banner, text="Install Service", width=110, height=26,
            command=self._install_service,
            fg_color="#5a3a00", hover_color="#7a5000",
        )
        # shown/hidden dynamically — not packed here

    def _show_error_banner(self, msg: str, show_install: bool = False) -> None:
        self._error_lbl.configure(text=msg)
        if show_install:
            self._btn_install.pack(side="right", padx=4)
        else:
            self._btn_install.pack_forget()
        self._error_banner.grid(row=1, column=0, sticky="ew")

    def _hide_error_banner(self) -> None:
        self._error_banner.grid_remove()

    # ── Tabs ──────────────────────────────────────────────────────────────────

    def _build_tabs(self) -> None:
        self._tabs = ctk.CTkTabview(self, corner_radius=8)
        self._tabs.grid(row=2, column=0, sticky="nsew", padx=8, pady=(4, 8))

        self._tabs.add("Dashboard")
        self._tabs.add("Settings")
        self._tabs.add("Logs")

        from src.gui.tabs.dashboard import DashboardTab
        from src.gui.tabs.settings  import SettingsTab
        from src.gui.tabs.logs      import LogsTab

        self._tab_dashboard = DashboardTab(self._tabs.tab("Dashboard"), self)
        self._tab_settings  = SettingsTab(self._tabs.tab("Settings"),   self)
        self._tab_logs      = LogsTab(self._tabs.tab("Logs"),           self)

    # ── Service control ───────────────────────────────────────────────────────

    def _install_service(self) -> None:
        self._show_error_banner("Installing service… please wait.", show_install=False)
        self.svc.install(self.config_path)

    def _start_service(self) -> None:
        if not self.svc.is_installed():
            self._show_error_banner(
                "Service not installed — click Install Service to register it.",
                show_install=True,
            )
            return
        self.svc.start()

    def _stop_service(self) -> None:
        self.svc.stop()

    def _restart_service(self) -> None:
        self.svc.restart()

    def restart_with_new_config(self) -> None:
        """Called by SettingsTab after saving config.yaml."""
        self._restart_service()

    # ── Service status callback (called from ServiceController thread) ────────

    def _on_svc_status_change(self, status: str, detail: str | None) -> None:
        self.after(0, lambda: self._apply_svc_status(status, detail))

    def _apply_svc_status(self, status: str, detail: str | None) -> None:
        from src.gui.service_controller import ServiceStatus

        colours = {
            ServiceStatus.NOT_INSTALLED: "gray",
            ServiceStatus.STOPPED:       _RED,
            ServiceStatus.STARTING:      _YELLOW,
            ServiceStatus.RUNNING:       _GREEN,
            ServiceStatus.STOPPING:      _YELLOW,
            ServiceStatus.UNKNOWN:       _MUTED,
        }
        labels = {
            ServiceStatus.NOT_INSTALLED: "Not Installed",
            ServiceStatus.STOPPED:       "Stopped",
            ServiceStatus.STARTING:      "Starting…",
            ServiceStatus.RUNNING:       "Running",
            ServiceStatus.STOPPING:      "Stopping…",
            ServiceStatus.UNKNOWN:       "Unknown",
        }
        self._lbl_svc.set(labels.get(status, status), colours.get(status, "gray"))

        if status == ServiceStatus.NOT_INSTALLED:
            self._show_error_banner(
                "Engine service not installed — click Install Service to register it.",
                show_install=True,
            )
        elif status == ServiceStatus.STOPPED and detail:
            self._show_error_banner(f"Service stopped: {detail}")
        elif status == ServiceStatus.RUNNING:
            self._hide_error_banner()

    def _refresh_service_status(self) -> None:
        """Poll service status every 5 s."""
        def _check() -> None:
            status = self.svc.query()
            self.after(0, lambda: self._apply_svc_status(status, None))
        threading.Thread(target=_check, daemon=True).start()
        self.after(5000, self._refresh_service_status)

    # ── Poll loop ─────────────────────────────────────────────────────────────

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
            self._lbl_gateway.set("Live", _GREEN)
            self._hide_error_banner()

        elif t == "_ws_disconnected":
            self._lbl_gateway.set("Offline", _MUTED)
            self._lbl_mt5.set("--", _MUTED)

        elif t == "STATE_SNAPSHOT":
            self._tab_dashboard.on_snapshot(payload)
            engine_info = payload.get("engine", {})
            mt5_ok      = payload.get("connected", False)
            gw_ok       = engine_info.get("connected_signal_engine", False)
            self._lbl_mt5.set(
                "Connected" if mt5_ok else "Connecting…",
                _GREEN if mt5_ok else _YELLOW,
            )
            self._lbl_gateway.set(
                "Active" if gw_ok else "Connecting…",
                _GREEN if gw_ok else _YELLOW,
            )
            if mt5_ok:
                self._hide_error_banner()

        elif t == "METRICS_UPDATE":
            self._tab_dashboard.on_metrics(payload)

        elif t == "mt5.error":
            # Engine couldn't connect to MT5 — show in banner, keep retrying
            msg = payload.get("message", "MT5 connection failed")
            # Strip the internal exception type prefix for readability
            if ":" in msg:
                msg = msg.split(":", 1)[-1].strip()
            self._lbl_mt5.set("Error", _RED)
            self._show_error_banner(f"MT5: {msg}")

        elif t in (
            "trade.opened", "trade.tp1_hit",
            "trade.tp2_hit", "trade.sl_hit", "trade.closed",
        ):
            self._tab_dashboard.on_trade_event(t, payload)

        elif t in ("signal.received", "signal.triggered", "risk.approved", "risk.rejected"):
            self._tab_dashboard.on_signal_event(t, payload)

    # ── WS command helper ─────────────────────────────────────────────────────

    def send_command(self, cmd_type: str, payload: dict | None = None) -> None:
        self.ws.send(cmd_type, payload or {})

    # ── Close ─────────────────────────────────────────────────────────────────

    def _on_close(self) -> None:
        """Close the control panel — the engine service keeps running."""
        self.ws.stop()
        self.destroy()

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


# ── Helpers ───────────────────────────────────────────────────────────────────

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


def resolve_config_path(argv: list[str]) -> str:
    """
    Locate config.yaml in priority order:

    1. Explicit CLI arg
    2. Next to the exe (installed / user-editable copy)
    3. Walk up 3 levels from the exe dir — covers dev layout where exe sits
       in dist/apex-quant-trader-agent/ and config is in execution-engine/
    4. PyInstaller _internal/ bundle (sys._MEIPASS) — shipped default
    5. CWD
    6. Walk up from __file__ — non-packaged dev / editable venv install
    7. Fallback "config.yaml" — caller shows the error
    """
    # 1 — explicit arg
    for arg in argv[1:]:
        if not arg.startswith("-") and arg.endswith(".yaml"):
            return arg

    exe_dir = Path(sys.executable).parent

    # 2 & 3 — next to exe then up to 3 parent dirs
    for depth in range(4):
        candidate = exe_dir
        for _ in range(depth):
            candidate = candidate.parent
        cfg = candidate / "config.yaml"
        if cfg.exists():
            return str(cfg)

    # 4 — PyInstaller bundle directory (onedir _internal/)
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bundled = Path(meipass) / "config.yaml"
        if bundled.exists():
            return str(bundled)

    # 5 — CWD
    cwd_cfg = Path.cwd() / "config.yaml"
    if cwd_cfg.exists():
        return str(cwd_cfg)

    # 6 — walk up from this source file (venv / editable-install)
    for parent in Path(__file__).resolve().parents:
        cfg = parent / "config.yaml"
        if cfg.exists():
            return str(cfg)

    # 7 — fallback
    return "config.yaml"
