"""
src/gui/pages/settings.py — General application settings

Sections:
  • Startup  — auto-start engine on Windows login, minimise to tray
  • Files    — open config/log/data folders
  • Account  — re-run setup wizard
"""
from __future__ import annotations

import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from typing import TYPE_CHECKING

import customtkinter as ctk

from src.gui.theme import (
    GREEN, RED, YELLOW, MUTED, TEXT, TEXT_SOFT,
    BASE, SURFACE, SURFACE_RAISED, LINE, LINE_STRONG,
    SUCCESS_BG, SUCCESS_BORDER, WARNING_BG, WARNING_BORDER,
    section_rule, page_header,
)
from src.gui.components import SectionCard, ActionBanner, PrimaryButton, InfoTable

if TYPE_CHECKING:
    from src.gui.app import ApexTraderGUI


class SettingsPage(ctk.CTkScrollableFrame):  # header scrolls; content is short enough

    def __init__(self, parent: tk.Widget, app: "ApexTraderGUI") -> None:
        super().__init__(parent, fg_color=SURFACE, corner_radius=0)
        self.app = app
        self._build()

    def _build(self) -> None:
        page_header(self, "Settings", "Application preferences and file locations")

        # ── Startup ────────────────────────────────────────────────────────────
        section_rule(self, "STARTUP BEHAVIOUR").pack(fill="x", padx=24, pady=(20, 8))

        cfg = self.app.config.load()
        startup = cfg.get("startup", {})

        startup_card = SectionCard(self)
        startup_card.pack(fill="x", padx=24)

        self._var_autostart = tk.BooleanVar(
            value=bool(startup.get("auto_start_engine", False)),
        )
        self._var_minimise  = tk.BooleanVar(
            value=bool(startup.get("minimise_on_start", False)),
        )

        def _row(label: str, detail: str, var: tk.BooleanVar) -> None:
            row = ctk.CTkFrame(startup_card.body, fg_color="transparent")
            row.pack(fill="x", pady=5)
            left = ctk.CTkFrame(row, fg_color="transparent")
            left.pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(
                left, text=label, anchor="w",
                font=ctk.CTkFont(size=13), text_color=TEXT,
            ).pack(anchor="w")
            ctk.CTkLabel(
                left, text=detail, anchor="w",
                font=ctk.CTkFont(size=11), text_color=MUTED,
            ).pack(anchor="w")
            ctk.CTkSwitch(
                row, text="", variable=var,
                onvalue=True, offvalue=False,
                command=self._save_startup,
            ).pack(side="right")

        _row("Start engine automatically",
             "Start the Apex trading engine when Windows logs in.",
             self._var_autostart)
        _row("Minimise on open",
             "Start this control panel minimised (engine still runs in background).",
             self._var_minimise)

        # Banner lives inside a padded wrapper so padx is preserved after
        # hide() / show() cycles (ActionBanner replays its pack kwargs on show).
        _banner_wrap = ctk.CTkFrame(self, fg_color="transparent")
        _banner_wrap.pack(fill="x", padx=24, pady=(4, 0))
        self._startup_banner = ActionBanner(_banner_wrap)
        self._startup_banner.hide()

        # ── Files and folders ─────────────────────────────────────────────────
        section_rule(self, "FILES & FOLDERS").pack(fill="x", padx=24, pady=(20, 8))

        files_card = SectionCard(self)
        files_card.pack(fill="x", padx=24)

        from src.gui.config_manager import ConfigManager
        self._cfg_path  = ConfigManager().path
        self._logs_dir  = ConfigManager.programdata_logs_path()
        self._data_dir  = ConfigManager.programdata_data_path()

        def _folder_row(label: str, path: Path, btn_label: str) -> None:
            row = ctk.CTkFrame(files_card.body, fg_color="transparent")
            row.pack(fill="x", pady=6)
            left = ctk.CTkFrame(row, fg_color="transparent")
            left.pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(left, text=label, anchor="w",
                font=ctk.CTkFont(size=13), text_color=TEXT).pack(anchor="w")
            ctk.CTkLabel(left, text=str(path), anchor="w",
                font=ctk.CTkFont(family="Consolas", size=10), text_color=MUTED,
                wraplength=460).pack(anchor="w", pady=(1, 0))
            ctk.CTkButton(
                row, text=btn_label, width=110, height=30,
                font=ctk.CTkFont(size=11),
                command=lambda p=path: self._open_folder(p),
            ).pack(side="right")

        _folder_row("Configuration file", self._cfg_path,   "Open folder")
        _folder_row("Log files",           self._logs_dir,   "Open folder")
        _folder_row("Data files",          self._data_dir,   "Open folder")

        # ── Setup & account ────────────────────────────────────────────────────
        section_rule(self, "SETUP & ACCOUNT").pack(fill="x", padx=24, pady=(20, 8))

        acct_card = SectionCard(self)
        acct_card.pack(fill="x", padx=24)

        def _action_row(label: str, detail: str, btn_label: str, fn, tone="info"):
            row = ctk.CTkFrame(acct_card.body, fg_color="transparent")
            row.pack(fill="x", pady=6)
            left = ctk.CTkFrame(row, fg_color="transparent")
            left.pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(left, text=label, anchor="w",
                font=ctk.CTkFont(size=13), text_color=TEXT).pack(anchor="w")
            ctk.CTkLabel(left, text=detail, anchor="w",
                font=ctk.CTkFont(size=11), text_color=MUTED,
                wraplength=400).pack(anchor="w")
            PrimaryButton(
                row, text=btn_label, tone=tone, width=130, height=30,
                command=fn,
            ).pack(side="right")

        _action_row(
            "Re-run setup wizard",
            "Start the setup wizard again to change your terminal, credentials, or activation key.",
            "Run Setup",
            lambda: self.app.navigate("__setup__"),
            tone="info",
        )
        _action_row(
            "Reload configuration",
            "Reload config.yaml from disk (useful if you edited it manually).",
            "Reload",
            self._reload_config,
            tone="info",
        )

        _acct_wrap = ctk.CTkFrame(self, fg_color="transparent")
        _acct_wrap.pack(fill="x", padx=24, pady=(4, 24))
        self._acct_banner = ActionBanner(_acct_wrap)
        self._acct_banner.hide()

        # ── Version info ───────────────────────────────────────────────────────
        section_rule(self, "ABOUT").pack(fill="x", padx=24, pady=(4, 8))

        about_card = SectionCard(self)
        about_card.pack(fill="x", padx=24, pady=(0, 24))

        t = InfoTable(about_card.body)
        import sys as _sys
        version = self._read_version()
        t.add_row("Apex Quant Trader",   version)
        t.add_row("Python",              _sys.version.split()[0])
        t.add_row("Config path",         str(self.app.config.path))
        t.pack(fill="x")

    # ── Actions ───────────────────────────────────────────────────────────────

    def _save_startup(self) -> None:
        err = self.app.config.update("startup", {
            "auto_start_engine": self._var_autostart.get(),
            "minimise_on_start": self._var_minimise.get(),
        })
        if err:
            self._startup_banner.show(err, "danger")
        else:
            # Auto-dismiss success notice after 3 s — it is informational only
            self._startup_banner.show(
                "Startup preferences saved.",
                "good",
                auto_dismiss_after_ms=3000,
            )

    def _open_folder(self, path: Path) -> None:
        target = path if path.is_dir() else path.parent
        try:
            target.mkdir(parents=True, exist_ok=True)
            subprocess.Popen(["explorer", str(target)])
        except Exception as exc:
            pass

    def _reload_config(self) -> None:
        self.app.config.reload()
        complete = self.app.config.is_setup_complete()
        self.app.app_state.mark_setup_complete(complete)
        self._acct_banner.show("Configuration reloaded.", "good", auto_dismiss_after_ms=3000)

    def _read_version(self) -> str:
        for c in (
            Path("version.txt"),
            Path(sys.executable).parent / "version.txt",
            Path(__file__).resolve().parent.parent.parent / "version.txt",
        ):
            try:
                return c.read_text(encoding="utf-8").strip()
            except Exception:
                pass
        return "?"

    # ── Broadcast callbacks ───────────────────────────────────────────────────

    def on_engine_status(self, status: str, detail=None) -> None:
        pass
