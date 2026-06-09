"""
src/gui/onboarding.py — First-run setup wizard.

The wizard is presented as a centered card (not full-screen).
On completion it calls on_complete() so app.py transitions to the dashboard.

Steps
-----
1  Welcome          — what Apex does; why the GUI exists
2  Trading Platform — scan + pick MetaTrader terminal
3  MT5 Account      — login, password, server
4  License Key      — activation key + connection server
5  Risk Profile     — daily loss %, streak, drawdown %
6  Install Engine   — register Windows service
7  Finish           — summary + "Start Engine" CTA
"""
from __future__ import annotations

import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

import customtkinter as ctk

from src.gui.theme import (
    GREEN, RED, YELLOW, MUTED, TEXT, TEXT_SOFT,
    SURFACE, SURFACE_RAISED, BASE, LINE, LINE_STRONG,
    SUCCESS_BG, SUCCESS_BORDER,
    DANGER_BG, WARNING_BG, WARNING_BORDER,
    INFO_BG, INFO_BORDER, INFO,
    section_rule, page_header,
)
from src.gui.components import (
    ActionBanner, PrimaryButton, SectionCard, labeled_field,
)

if TYPE_CHECKING:
    from src.gui.config_manager import ConfigManager
    from src.gui.installer import InstallerService


_TOTAL_STEPS  = 7
_CARD_WIDTH   = 840   # max card width (px); shrinks on narrow windows
_STEP_H       = 420   # fixed height for the scrollable step area

# Total card height: top(4) + header(52) + divider(1) + content(420)
#                   + divider(1) + footer(60)
_CARD_HEIGHT  = 4 + 52 + 1 + _STEP_H + 1 + 60  # = 538


# ── Wizard shell ──────────────────────────────────────────────────────────────

class OnboardingWizard(ctk.CTkFrame):
    """
    Full-window frame containing a centered card.
    Call .start() to show step 1.
    """

    def __init__(
        self,
        parent: tk.Widget,
        config: "ConfigManager",
        installer: "InstallerService",
        on_complete: Callable,
    ) -> None:
        super().__init__(parent, fg_color=BASE, corner_radius=0)
        self._cfg       = config
        self._installer = installer
        self._done_cb   = on_complete
        self._step      = 0
        self._step_frames: list[_WizardStep] = []
        self._current_frame: Optional[_WizardStep] = None
        self._data: dict = {}

        # Card shell — fixed size, centered with place(relx/rely=0.5).
        # pack_propagate(False) locks the explicit width/height so children
        # using fill="x" actually fill the card rather than collapsing it.
        self._card = ctk.CTkFrame(
            self,
            width=640,
            height=_CARD_HEIGHT,
            fg_color=SURFACE_RAISED,
            corner_radius=10,
            border_width=1,
            border_color=LINE_STRONG,
        )
        self._card.pack_propagate(False)
        self._card.place(relx=0.5, rely=0.5, anchor="center")
        self.bind("<Configure>", self._on_outer_resize)

        self._build_chrome()
        self._build_steps()

    def _on_outer_resize(self, event: tk.Event) -> None:
        # Resize card width with window, keep height fixed.
        w = min(_CARD_WIDTH, max(560, event.width - 80))
        self._card.configure(width=w)

    def start(self) -> None:
        self._goto(0)

    # ── Chrome ────────────────────────────────────────────────────────────────

    def _build_chrome(self) -> None:
        card = self._card

        # Progress bar (top accent)
        top = ctk.CTkFrame(card, height=4, fg_color=BASE, corner_radius=0)
        top.pack(fill="x")
        top.pack_propagate(False)
        self._progress_bar = ctk.CTkFrame(top, height=4, fg_color=GREEN, corner_radius=0)
        self._progress_bar.place(x=0, y=0, relheight=1.0, relwidth=0.0)

        # Header
        hdr = ctk.CTkFrame(card, height=52, fg_color="transparent", corner_radius=0)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        ctk.CTkFrame(hdr, width=3, fg_color=GREEN, corner_radius=0).pack(
            side="left", fill="y",
        )
        self._hdr_title = ctk.CTkLabel(
            hdr, text="Setup",
            font=ctk.CTkFont(size=15, weight="bold"), text_color=TEXT,
        )
        self._hdr_title.pack(side="left", padx=14)
        self._step_lbl = ctk.CTkLabel(
            hdr, text="Step 1 of 7",
            font=ctk.CTkFont(size=11), text_color=MUTED,
        )
        self._step_lbl.pack(side="right", padx=16)

        ctk.CTkFrame(card, height=1, fg_color=LINE, corner_radius=0).pack(fill="x")

        # Content area — fixed height, scrollable inside
        self._content = ctk.CTkFrame(card, fg_color="transparent", height=_STEP_H)
        self._content.pack(fill="x")
        self._content.pack_propagate(False)

        ctk.CTkFrame(card, height=1, fg_color=LINE, corner_radius=0).pack(fill="x")

        # Footer
        footer = ctk.CTkFrame(card, height=60, fg_color="transparent", corner_radius=0)
        footer.pack(fill="x")
        footer.pack_propagate(False)

        btn_area = ctk.CTkFrame(footer, fg_color="transparent")
        btn_area.pack(fill="both", expand=True, padx=20)

        self._btn_back = ctk.CTkButton(
            btn_area, text="← Back", width=100, height=34,
            font=ctk.CTkFont(size=12),
            fg_color="transparent", hover_color=LINE_STRONG,
            border_width=1, border_color=LINE,
            text_color=MUTED,
            command=self._back,
        )
        self._btn_back.pack(side="left", pady=12)

        self._btn_skip = ctk.CTkButton(
            btn_area, text="Skip for now", width=110, height=34,
            font=ctk.CTkFont(size=12),
            fg_color="transparent", hover_color=LINE_STRONG,
            border_width=1, border_color=LINE,
            text_color=MUTED,
            command=self._skip,
        )
        self._btn_skip.pack(side="left", padx=(8, 0), pady=12)

        self._btn_next = PrimaryButton(
            btn_area, text="Continue →", width=140, height=34, tone="good",
            command=self._next,
        )
        self._btn_next.pack(side="right", pady=12)

    # ── Steps ─────────────────────────────────────────────────────────────────

    def _build_steps(self) -> None:
        self._step_frames = [
            _StepWelcome(self._content, self),
            _StepPlatform(self._content, self),
            _StepAccount(self._content, self),
            _StepActivation(self._content, self),
            _StepRisk(self._content, self),
            _StepInstall(self._content, self, self._installer),
            _StepFinish(self._content, self),
        ]

    # ── Navigation ────────────────────────────────────────────────────────────

    def _goto(self, idx: int) -> None:
        if self._current_frame:
            self._current_frame.pack_forget()

        self._step = idx
        frame = self._step_frames[idx]
        frame.pack(fill="both", expand=True)
        frame.on_enter(self._cfg.load(), self._data)
        self._current_frame = frame

        step_num = idx + 1
        self._step_lbl.configure(text=f"Step {step_num} of {_TOTAL_STEPS}")
        self._hdr_title.configure(text=frame.title)
        self._progress_bar.place(
            x=0, y=0, relheight=1.0, relwidth=step_num / _TOTAL_STEPS,
        )
        self._btn_back.configure(state="normal" if idx > 0 else "disabled")

        if idx == _TOTAL_STEPS - 1:
            self._btn_next.configure(text="Finish  ✓")
            self._btn_skip.pack_forget()
        else:
            self._btn_next.configure(text="Continue →")
            if frame.skippable:
                self._btn_skip.pack(side="left", padx=(8, 0), pady=12)
            else:
                self._btn_skip.pack_forget()

    def _next(self) -> None:
        frame = self._step_frames[self._step]
        ok, _ = frame.validate_and_save(self._cfg, self._data)
        if not ok:
            return
        if self._step == _TOTAL_STEPS - 1:
            self._finish()
        else:
            self._goto(self._step + 1)

    def _back(self) -> None:
        if self._step > 0:
            self._goto(self._step - 1)

    def _skip(self) -> None:
        if self._step < _TOTAL_STEPS - 1:
            self._goto(self._step + 1)

    def _finish(self) -> None:
        try:
            self._done_cb()
        except Exception:
            pass

    def navigate_to_step(self, idx: int) -> None:
        self._goto(idx)

    # ── Dashboard helper (shared by steps) ────────────────────────────────────

    def open_dashboard(self) -> None:
        webbrowser.open(self._cfg.dashboard_url())


# ── Base step ─────────────────────────────────────────────────────────────────

class _WizardStep(ctk.CTkScrollableFrame):
    title:     str  = "Setup"
    skippable: bool = False

    def __init__(self, parent: tk.Widget, wizard: OnboardingWizard) -> None:
        super().__init__(parent, fg_color="transparent", corner_radius=0)
        self.wizard = wizard
        self._build()

    def _build(self) -> None:
        pass

    def on_enter(self, cfg: dict, data: dict) -> None:
        pass

    def validate_and_save(
        self,
        config: "ConfigManager",
        data: dict,
    ) -> tuple[bool, str]:
        return True, ""


# ── Step 1 — Welcome ──────────────────────────────────────────────────────────

class _StepWelcome(_WizardStep):
    title     = "Welcome to Apex Quantel"
    skippable = False

    def _build(self) -> None:
        outer = ctk.CTkFrame(self, fg_color="transparent")
        outer.pack(fill="x", padx=48, pady=(24, 16))

        # Logo image if available, otherwise emoji
        try:
            from src.gui.assets import load_logo_image
            logo_img = load_logo_image(size=(56, 56))
        except Exception:
            logo_img = None

        if logo_img:
            ctk.CTkLabel(outer, image=logo_img, text="",
                         fg_color="transparent").pack(pady=(0, 8))
        else:
            ctk.CTkLabel(outer, text="⚡",
                         font=ctk.CTkFont(size=52), text_color=GREEN).pack(pady=(0, 8))

        ctk.CTkLabel(
            outer, text="Apex Quantel",
            font=ctk.CTkFont(size=26, weight="bold"), text_color=TEXT,
        ).pack()

        ctk.CTkLabel(
            outer, text="Automated trading infrastructure",
            font=ctk.CTkFont(size=13), text_color=MUTED,
        ).pack(pady=(4, 20))

        for icon, heading, body in [
            ("🔄", "AQ Agent",
             "AQ Agent runs as a background task that connects to MetaTrader 5, "
             "receives trading signals, and executes trades automatically — "
             "even when this control panel is closed."),
            ("🖥️", "This control panel",
             "This app lets you configure, install, start, and monitor AQ Agent. "
             "You do not need to keep it open while trading."),
            ("📋", "First-time setup",
             "This wizard will guide you through selecting your MetaTrader "
             "terminal, entering your license key, and installing the "
             "background service. It takes about two minutes."),
        ]:
            card = ctk.CTkFrame(
                outer, corner_radius=8,
                fg_color=BASE, border_width=1, border_color=LINE,
            )
            card.pack(fill="x", pady=4)
            row = ctk.CTkFrame(card, fg_color="transparent")
            row.pack(padx=14, pady=10, fill="x")
            ctk.CTkLabel(
                row, text=icon, font=ctk.CTkFont(size=18), width=32,
            ).pack(side="left", anchor="n", pady=2)
            col = ctk.CTkFrame(row, fg_color="transparent")
            col.pack(side="left", fill="x", expand=True, padx=(10, 0))
            ctk.CTkLabel(
                col, text=heading, anchor="w",
                font=ctk.CTkFont(size=13, weight="bold"), text_color=TEXT,
            ).pack(anchor="w")
            ctk.CTkLabel(
                col, text=body, anchor="w",
                font=ctk.CTkFont(size=11), text_color=MUTED,
                justify="left", wraplength=580,
            ).pack(anchor="w", pady=(2, 0))


# ── Step 2 — Trading Platform ─────────────────────────────────────────────────

class _StepPlatform(_WizardStep):
    title     = "Select Your Trading Platform"
    skippable = True

    def _build(self) -> None:
        self._selected_id:   Optional[str] = None
        self._selected_path: Optional[str] = None
        self._installs:      list          = []
        self._card_frames:   dict          = {}

        intro = ctk.CTkFrame(self, fg_color="transparent")
        intro.pack(fill="x", padx=32, pady=(20, 0))
        ctk.CTkLabel(
            intro,
            text="Apex requires MetaTrader 5. Select your broker's terminal below.",
            font=ctk.CTkFont(size=13), text_color=TEXT_SOFT,
            wraplength=680, justify="left",
        ).pack(anchor="w")

        scan_row = ctk.CTkFrame(self, fg_color="transparent")
        scan_row.pack(fill="x", padx=32, pady=(14, 6))
        self._scan_lbl = ctk.CTkLabel(
            scan_row, text="Scanning…",
            font=ctk.CTkFont(size=12), text_color=MUTED,
        )
        self._scan_lbl.pack(side="left")
        ctk.CTkButton(
            scan_row, text="↺  Scan again", width=110, height=28,
            command=self._scan,
        ).pack(side="right")

        self._cards_wrap = ctk.CTkFrame(self, fg_color="transparent")
        self._cards_wrap.pack(fill="x", padx=32, pady=(0, 12))

        self._banner = ActionBanner(self)
        self._banner.pack(fill="x", padx=32, pady=(0, 4))
        self._banner.hide()

        self._adv_visible = False
        ctk.CTkButton(
            self, text="▶  Manual path (advanced)",
            anchor="w", height=26, width=220,
            fg_color="transparent", hover_color=LINE_STRONG,
            text_color=MUTED, font=ctk.CTkFont(size=11),
            command=self._toggle_adv,
        ).pack(anchor="w", padx=32)

        self._adv_frame = ctk.CTkFrame(
            self, fg_color=BASE,
            corner_radius=6, border_width=1, border_color=LINE,
        )
        adv_inner = ctk.CTkFrame(self._adv_frame, fg_color="transparent")
        adv_inner.pack(padx=12, pady=10, fill="x")

        path_row = ctk.CTkFrame(adv_inner, fg_color="transparent")
        path_row.pack(fill="x")
        ctk.CTkLabel(
            path_row, text="Path:", width=60, anchor="w",
            font=ctk.CTkFont(size=11), text_color=MUTED,
        ).pack(side="left")
        self._var_path = tk.StringVar()
        ctk.CTkEntry(
            path_row, textvariable=self._var_path, width=360,
            font=ctk.CTkFont(family="Consolas", size=11),
        ).pack(side="left", padx=(4, 4))
        ctk.CTkButton(
            path_row, text="Browse…", width=80, height=26,
            command=self._browse,
        ).pack(side="left")

    def on_enter(self, cfg: dict, data: dict) -> None:
        saved = cfg.get("mt5", {}).get("path", "")
        self._var_path.set(saved)
        if self._installs:
            for inst in self._installs:
                if inst.exe_path.lower() == saved.lower():
                    self._selected_id   = inst.id
                    self._selected_path = inst.exe_path
                    break
            self._refresh_card_borders()
        else:
            self._scan()

    def _scan(self) -> None:
        self._scan_lbl.configure(text="Scanning for MetaTrader installations…", text_color=MUTED)
        for w in self._cards_wrap.winfo_children():
            w.destroy()

        def _do():
            from src.gui.mt5_detector import detect_installs
            results = detect_installs()
            self._cards_wrap.after(0, lambda: self._on_scan_done(results))

        threading.Thread(target=_do, daemon=True).start()

    def _on_scan_done(self, installs: list) -> None:
        self._installs = installs
        for w in self._cards_wrap.winfo_children():
            w.destroy()
        self._card_frames.clear()

        if not installs:
            self._scan_lbl.configure(
                text="No MetaTrader installations found. Use Manual path below.",
                text_color=YELLOW,
            )
            ctk.CTkLabel(
                self._cards_wrap,
                text="MetaTrader 5 was not found. Install it from your broker's "
                     "website, then click Scan again.",
                font=ctk.CTkFont(size=12), text_color=MUTED, justify="left",
            ).pack(anchor="w", pady=8)
            return

        count = len(installs)
        self._scan_lbl.configure(
            text=f"Found {count} installation{'s' if count != 1 else ''}",
            text_color=GREEN,
        )

        saved = self._var_path.get().strip()
        for inst in installs:
            if inst.exe_path.lower() == saved.lower():
                self._selected_id   = inst.id
                self._selected_path = inst.exe_path
                break

        for inst in installs:
            card = ctk.CTkFrame(
                self._cards_wrap, corner_radius=8,
                fg_color=SURFACE_RAISED, border_width=2, border_color=LINE,
            )
            card.pack(fill="x", pady=4)
            self._card_frames[inst.id] = card

            inner = ctk.CTkFrame(card, fg_color="transparent")
            inner.pack(padx=14, pady=10, fill="x")

            badge_text = "MT5" if inst.platform == "mt5" else "MT4"
            ctk.CTkLabel(
                inner, text=badge_text,
                font=ctk.CTkFont(size=10, weight="bold"),
                fg_color="#1a2a4a", corner_radius=4,
                width=34, height=22, text_color="#6699cc",
            ).pack(side="left", padx=(0, 10))

            col = ctk.CTkFrame(inner, fg_color="transparent")
            col.pack(side="left", fill="x", expand=True)

            ctk.CTkLabel(
                col, text=inst.name,
                font=ctk.CTkFont(size=14, weight="bold"), text_color=TEXT, anchor="w",
            ).pack(anchor="w")

            st_text  = "Ready" if inst.is_available else "Not available"
            st_color = GREEN if inst.is_available else RED
            ctk.CTkLabel(
                col, text=st_text,
                font=ctk.CTkFont(size=11), text_color=st_color, anchor="w",
            ).pack(anchor="w")

            ctk.CTkButton(
                inner, text="Select", width=90, height=30,
                command=lambda i=inst.id, p=inst.exe_path: self._select(i, p),
            ).pack(side="right")

        self._refresh_card_borders()

    def _select(self, install_id: str, path: str) -> None:
        self._selected_id   = install_id
        self._selected_path = path
        self._var_path.set(path)
        self._refresh_card_borders()
        self._banner.hide()

    def _refresh_card_borders(self) -> None:
        for iid, card in self._card_frames.items():
            card.configure(border_color=GREEN if iid == self._selected_id else LINE)

    def _toggle_adv(self) -> None:
        self._adv_visible = not self._adv_visible
        if self._adv_visible:
            self._adv_frame.pack(fill="x", padx=32, pady=(4, 12))
        else:
            self._adv_frame.pack_forget()

    def _browse(self) -> None:
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            title="Select MetaTrader executable",
            filetypes=[
                ("MT5 executable", "terminal64.exe"),
                ("MT4 executable", "terminal.exe"),
                ("All executables", "*.exe"),
            ],
        )
        if path:
            self._var_path.set(path.replace("/", "\\"))
            self._selected_id   = None
            self._selected_path = path.replace("/", "\\")
            self._refresh_card_borders()

    def validate_and_save(self, config: "ConfigManager", data: dict) -> tuple:
        path = self._selected_path or self._var_path.get().strip()
        if not path:
            self._banner.show(
                "Please select a MetaTrader terminal before continuing, "
                "or click Skip to come back later.",
                "warn",
            )
            return False, "No terminal selected"
        if not Path(path).exists():
            self._banner.show(
                "The selected terminal executable no longer exists. "
                "Scan again or use Browse to locate it.",
                "danger",
            )
            return False, "Path not found"
        data["mt5_path"] = path
        error = config.update("mt5", {"path": path})
        if error:
            self._banner.show(error, "danger")
            return False, error
        self._banner.hide()
        return True, ""


# ── Step 3 — MT5 Account ──────────────────────────────────────────────────────

class _StepAccount(_WizardStep):
    title     = "MetaTrader Account Credentials"
    skippable = False

    def _build(self) -> None:
        f = ctk.CTkFrame(self, fg_color="transparent")
        f.pack(fill="x", padx=40, pady=(20, 0))

        ctk.CTkLabel(
            f,
            text="Enter your MetaTrader 5 account details.\n"
                 "These are the same credentials you use to log into MT5.",
            font=ctk.CTkFont(size=13), text_color=TEXT_SOFT,
            justify="left",
        ).pack(anchor="w", pady=(0, 16))

        card = SectionCard(f)
        card.pack(fill="x", pady=(0, 12))

        self._var_login    = tk.StringVar()
        self._var_password = tk.StringVar()
        self._var_server   = tk.StringVar()

        labeled_field(card.body, "Account number", self._var_login,
                      placeholder="e.g. 12345678")
        labeled_field(card.body, "Password",       self._var_password, masked=True)
        labeled_field(card.body, "Server",         self._var_server,
                      placeholder="e.g. FBS-Real")

        ctk.CTkLabel(
            f,
            text="Your credentials are stored locally and never transmitted "
                 "except to MetaTrader itself.",
            font=ctk.CTkFont(size=11), text_color=MUTED,
            wraplength=620, justify="left",
        ).pack(anchor="w", pady=(4, 0))

        self._banner = ActionBanner(f)
        self._banner.pack(fill="x", pady=(8, 0))
        self._banner.hide()

    def on_enter(self, cfg: dict, data: dict) -> None:
        mt5 = cfg.get("mt5", {})
        self._var_login.set(str(mt5.get("login", "")))
        self._var_password.set(str(mt5.get("password", "")))
        self._var_server.set(str(mt5.get("server", "")))

    def validate_and_save(self, config: "ConfigManager", data: dict) -> tuple:
        login_str = self._var_login.get().strip()
        password  = self._var_password.get().strip()
        server    = self._var_server.get().strip()

        if not login_str:
            self._banner.show("Account number is required.", "warn"); return False, ""
        if not password:
            self._banner.show("Password is required.", "warn"); return False, ""
        if not server:
            self._banner.show("Server name is required.", "warn"); return False, ""
        try:
            login_int = int(login_str)
        except ValueError:
            self._banner.show("Account number must be a number (e.g. 12345678).", "warn")
            return False, ""
        err = config.update("mt5", {
            "login": login_int, "password": password, "server": server,
        })
        if err:
            self._banner.show(err, "danger"); return False, err
        self._banner.hide()
        return True, ""


# ── Step 4 — License Key ──────────────────────────────────────────────────────

class _StepActivation(_WizardStep):
    title     = "License Key"
    skippable = False

    def _build(self) -> None:
        f = ctk.CTkFrame(self, fg_color="transparent")
        f.pack(fill="x", padx=40, pady=(20, 0))

        # ── Dashboard callout ──────────────────────────────────────────────
        callout = ctk.CTkFrame(
            f, fg_color=INFO_BG,
            corner_radius=8, border_width=1, border_color=INFO_BORDER,
        )
        callout.pack(fill="x", pady=(0, 16))

        inner = ctk.CTkFrame(callout, fg_color="transparent")
        inner.pack(padx=16, pady=12, fill="x")

        ctk.CTkLabel(
            inner,
            text="You need an active license key to use Apex Quantel.",
            font=ctk.CTkFont(size=13, weight="bold"), text_color=INFO, anchor="w",
        ).pack(anchor="w")
        ctk.CTkLabel(
            inner,
            text="Purchase or manage your license key from the Apex web dashboard.",
            font=ctk.CTkFont(size=12), text_color=TEXT_SOFT, anchor="w",
            wraplength=680, justify="left",
        ).pack(anchor="w", pady=(4, 8))

        PrimaryButton(
            inner, text="Open Web Dashboard", tone="info",
            width=200, height=34,
            command=self.wizard.open_dashboard,
        ).pack(anchor="w")

        # ── Key input ──────────────────────────────────────────────────────
        ctk.CTkLabel(
            f,
            text="Already have a key? Enter it below:",
            font=ctk.CTkFont(size=12), text_color=MUTED,
        ).pack(anchor="w", pady=(0, 8))

        key_card = SectionCard(f)
        key_card.pack(fill="x", pady=(0, 8))

        self._var_key = tk.StringVar()
        labeled_field(
            key_card.body, "License key", self._var_key,
            masked=True, placeholder="Paste your license key here",
            width=320,
        )

        # ── Connection server (advanced toggle) ────────────────────────────
        self._adv_visible = False
        ctk.CTkButton(
            f, text="▶  Advanced (connection server)",
            anchor="w", height=26, width=260,
            fg_color="transparent", hover_color=LINE_STRONG,
            text_color=MUTED, font=ctk.CTkFont(size=11),
            command=self._toggle_adv,
        ).pack(anchor="w", pady=(4, 0))

        self._adv_frame = ctk.CTkFrame(
            f, fg_color=BASE,
            corner_radius=6, border_width=1, border_color=LINE,
        )
        adv_inner = ctk.CTkFrame(self._adv_frame, fg_color="transparent")
        adv_inner.pack(padx=12, pady=10, fill="x")

        ctk.CTkLabel(
            adv_inner,
            text="Connection server",
            font=ctk.CTkFont(size=12, weight="bold"), text_color=TEXT,
            anchor="w",
        ).pack(anchor="w")
        ctk.CTkLabel(
            adv_inner,
            text="The WebSocket server address from your account dashboard. "
                 "Leave blank to use the pre-configured default.",
            font=ctk.CTkFont(size=11), text_color=MUTED,
            wraplength=600, justify="left",
        ).pack(anchor="w", pady=(2, 8))

        self._var_url = tk.StringVar()
        url_row = ctk.CTkFrame(adv_inner, fg_color="transparent")
        url_row.pack(fill="x")
        ctk.CTkEntry(
            url_row, textvariable=self._var_url, width=400,
            font=ctk.CTkFont(family="Consolas", size=11),
            placeholder_text="wss://gateway.somicast.com",
        ).pack(side="left")

        self._banner = ActionBanner(f)
        self._banner.pack(fill="x", pady=(8, 0))
        self._banner.hide()

    def on_enter(self, cfg: dict, data: dict) -> None:
        gw = cfg.get("gateway", {})
        self._var_url.set(str(gw.get("ws_url", "")))
        self._var_key.set(str(gw.get("activation_key", "")))

    def _toggle_adv(self) -> None:
        self._adv_visible = not self._adv_visible
        if self._adv_visible:
            self._adv_frame.pack(fill="x", pady=(6, 0))
        else:
            self._adv_frame.pack_forget()

    def validate_and_save(self, config: "ConfigManager", data: dict) -> tuple:
        key = self._var_key.get().strip()
        url = self._var_url.get().strip()

        if not key:
            self._banner.show(
                "License key is required. Open the web dashboard to get yours.",
                "warn",
            )
            return False, ""

        # If no URL supplied, use config default or leave as-is
        if not url:
            existing = config.get("gateway", "ws_url") or ""
            url = existing

        err = config.update("gateway", {"activation_key": key, "ws_url": url})
        if err:
            self._banner.show(err, "danger"); return False, err
        self._banner.hide()
        return True, ""


# ── Step 5 — Risk Profile ─────────────────────────────────────────────────────

class _StepRisk(_WizardStep):
    title     = "Risk Profile"
    skippable = False

    def _build(self) -> None:
        f = ctk.CTkFrame(self, fg_color="transparent")
        f.pack(fill="x", padx=40, pady=(20, 0))

        ctk.CTkLabel(
            f,
            text="Set how much of your account AQ Agent is allowed to risk each day. "
                 "These limits can be changed at any time on the Risk page.",
            font=ctk.CTkFont(size=13), text_color=TEXT_SOFT,
            wraplength=700, justify="left",
        ).pack(anchor="w", pady=(0, 16))

        card = SectionCard(f)
        card.pack(fill="x", pady=(0, 10))

        self._var_daily_pct = tk.StringVar(value="2.5")
        self._var_streak    = tk.StringVar(value="3")
        self._var_drawdown  = tk.StringVar(value="5.0")

        def _field(parent, label, var, unit, tip):
            row = ctk.CTkFrame(parent, fg_color="transparent")
            row.pack(fill="x", pady=5)
            left = ctk.CTkFrame(row, fg_color="transparent")
            left.pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(left, text=label, anchor="w",
                font=ctk.CTkFont(size=13), text_color=TEXT).pack(anchor="w")
            ctk.CTkLabel(left, text=tip, anchor="w",
                font=ctk.CTkFont(size=11), text_color=MUTED,
                justify="left", wraplength=440).pack(anchor="w", pady=(1, 0))
            right = ctk.CTkFrame(row, fg_color="transparent")
            right.pack(side="right", padx=(12, 0))
            ctk.CTkEntry(right, textvariable=var, width=72,
                font=ctk.CTkFont(family="Consolas", size=13),
                justify="center").pack(side="left")
            ctk.CTkLabel(right, text=unit,
                font=ctk.CTkFont(size=12), text_color=MUTED).pack(side="left", padx=(6, 0))

        _field(card.body, "Daily loss limit",
               self._var_daily_pct, "%",
               "Maximum % of your account balance to lose per day.")
        _field(card.body, "Max losing streak",
               self._var_streak, "trades",
               "Worst-case consecutive losses. Used to size each trade.")
        _field(card.body, "Max account drawdown",
               self._var_drawdown, "%",
               "Engine pauses if total drawdown reaches this level.")

        formula_row = ctk.CTkFrame(
            f, fg_color=BASE, corner_radius=6,
            border_width=1, border_color=LINE,
        )
        formula_row.pack(fill="x", pady=(0, 8))
        inner = ctk.CTkFrame(formula_row, fg_color="transparent")
        inner.pack(padx=14, pady=8, fill="x")
        self._formula_lbl = ctk.CTkLabel(
            inner, text="Enter values above to see your risk per trade",
            font=ctk.CTkFont(size=12), text_color=MUTED,
        )
        self._formula_lbl.pack(anchor="w")

        for var in (self._var_daily_pct, self._var_streak):
            var.trace_add("write", self._update_formula)

        self._banner = ActionBanner(f)
        self._banner.pack(fill="x", pady=(4, 0))
        self._banner.hide()

    def _update_formula(self, *_) -> None:
        try:
            pct    = float(self._var_daily_pct.get())
            streak = int(float(self._var_streak.get()))
            if streak < 1:
                return
            per_trade = pct / streak
            self._formula_lbl.configure(
                text=f"Each trade risks {per_trade:.2f}% of your account  "
                     f"({pct:.1f}% daily budget ÷ {streak} trades)",
                text_color=TEXT_SOFT,
            )
        except Exception:
            self._formula_lbl.configure(
                text="Enter values above to see your risk per trade",
                text_color=MUTED,
            )

    def on_enter(self, cfg: dict, data: dict) -> None:
        risk = cfg.get("risk", {})
        if risk.get("max_daily_loss_percent"):
            self._var_daily_pct.set(str(risk["max_daily_loss_percent"]))
        if risk.get("max_losing_streak"):
            self._var_streak.set(str(risk["max_losing_streak"]))
        if risk.get("max_equity_drawdown_percent"):
            self._var_drawdown.set(str(risk["max_equity_drawdown_percent"]))
        self._update_formula()

    def validate_and_save(self, config: "ConfigManager", data: dict) -> tuple:
        try:
            pct      = float(self._var_daily_pct.get())
            streak   = int(float(self._var_streak.get()))
            drawdown = float(self._var_drawdown.get())
        except ValueError:
            self._banner.show("All risk values must be numbers.", "warn")
            return False, ""
        if not (0.1 <= pct <= 50):
            self._banner.show("Daily loss limit must be between 0.1% and 50%.", "warn")
            return False, ""
        if not (1 <= streak <= 20):
            self._banner.show("Max losing streak must be between 1 and 20.", "warn")
            return False, ""
        err = config.update("risk", {
            "max_daily_loss_percent":      pct,
            "max_losing_streak":           streak,
            "max_equity_drawdown_percent": drawdown,
        })
        if err:
            self._banner.show(err, "danger"); return False, err
        self._banner.hide()
        return True, ""


# ── Step 6 — Install Engine ───────────────────────────────────────────────────

class _StepInstall(_WizardStep):
    title     = "Install AQ Agent"
    skippable = False

    def __init__(
        self,
        parent: tk.Widget,
        wizard: OnboardingWizard,
        installer: "InstallerService",
    ) -> None:
        self._installer = installer
        super().__init__(parent, wizard)

    def _build(self) -> None:
        f = ctk.CTkFrame(self, fg_color="transparent")
        f.pack(fill="x", padx=40, pady=(20, 0))

        ctk.CTkLabel(
            f,
            text="AQ Agent runs as a Windows background service so it can trade "
                 "even when this control panel is closed.",
            font=ctk.CTkFont(size=13), text_color=TEXT_SOFT,
            wraplength=700, justify="left",
        ).pack(anchor="w", pady=(0, 16))

        status_card = SectionCard(f)
        status_card.pack(fill="x", pady=(0, 14))

        self._status_lbl = ctk.CTkLabel(
            status_card.body, text="Checking…",
            font=ctk.CTkFont(size=14, weight="bold"), text_color=MUTED,
        )
        self._status_lbl.pack(anchor="w")

        self._status_detail = ctk.CTkLabel(
            status_card.body, text="",
            font=ctk.CTkFont(size=12), text_color=MUTED,
            justify="left",
        )
        self._status_detail.pack(anchor="w", pady=(4, 0))

        self._btn_install = PrimaryButton(
            f, text="Install AQ Agent", tone="good", width=240,
            command=self._install,
        )
        self._btn_install.pack(anchor="w", pady=(0, 8))

        ctk.CTkLabel(
            f,
            text="Administrator permission is required. "
                 "A Windows security prompt will appear — click Yes to continue.",
            font=ctk.CTkFont(size=11), text_color=MUTED,
            justify="left",
        ).pack(anchor="w", pady=(4, 0))

        self._banner = ActionBanner(f)
        self._banner.pack(fill="x", pady=(10, 0))
        self._banner.hide()

    def on_enter(self, cfg: dict, data: dict) -> None:
        self._check_service()

    def _check_service(self) -> None:
        from src.gui.service_controller import ServiceController, ServiceStatus
        status    = ServiceController().query()
        installed = status != ServiceStatus.NOT_INSTALLED
        if installed:
            self._status_lbl.configure(
                text="✓  AQ Agent is installed", text_color=GREEN,
            )
            self._status_detail.configure(
                text="AQ Agent is registered and ready.",
            )
            self._btn_install.configure(text="Reinstall", state="normal")
        else:
            self._status_lbl.configure(
                text="AQ Agent is not installed yet", text_color=YELLOW,
            )
            self._status_detail.configure(
                text="Click Install below to register AQ Agent as a scheduled task.",
            )
            self._btn_install.configure(text="Install AQ Agent", state="normal")

    def _install(self) -> None:
        self._btn_install.configure(state="disabled", text="Installing…")
        self._banner.hide()

        def _on_result(ok: bool, msg: str) -> None:
            def _apply():
                if ok:
                    self._banner.show(msg, "good")
                    self._check_service()
                    self._btn_install.configure(state="normal")
                else:
                    self._banner.show(msg, "danger")
                    self._btn_install.configure(state="normal", text="Try Again")
            self.after(0, _apply)

        self._installer.on_result = _on_result
        from src.gui.config_manager import ConfigManager
        cfg = ConfigManager()
        self._installer.install_async(str(cfg.path))

    def validate_and_save(self, config: "ConfigManager", data: dict) -> tuple:
        return True, ""


# ── Step 7 — Finish ───────────────────────────────────────────────────────────

class _StepFinish(_WizardStep):
    title     = "Setup Complete"
    skippable = False

    def _build(self) -> None:
        self._content = ctk.CTkFrame(self, fg_color="transparent")
        self._content.pack(fill="x", padx=40, pady=(20, 0))

    def on_enter(self, cfg: dict, data: dict) -> None:
        for w in self._content.winfo_children():
            w.destroy()

        ctk.CTkLabel(
            self._content, text="✓  Setup complete",
            font=ctk.CTkFont(size=22, weight="bold"), text_color=GREEN,
        ).pack(anchor="w", pady=(0, 4))
        ctk.CTkLabel(
            self._content,
            text="Here is a summary of your configuration. "
                 "Click Finish to open the main dashboard.",
            font=ctk.CTkFont(size=13), text_color=TEXT_SOFT,
            justify="left",
        ).pack(anchor="w", pady=(0, 16))

        mt5  = cfg.get("mt5", {})
        gw   = cfg.get("gateway", {})
        risk = cfg.get("risk", {})

        from src.gui.service_controller import ServiceController, ServiceStatus
        svc_installed = ServiceController().query() != ServiceStatus.NOT_INSTALLED

        items = [
            ("Trading platform",  mt5.get("path", "—").split("\\")[-2] if mt5.get("path") else "—"),
            ("MT5 account",       f"{mt5.get('login', '—')} @ {mt5.get('server', '—')}"),
            ("License key",       "Saved  ✓" if gw.get("activation_key") else "Not set"),
            ("Daily loss limit",  f"{risk.get('max_daily_loss_percent', '—')}%"),
            ("AQ Agent",          "Installed  ✓" if svc_installed else "Not installed"),
        ]
        card = SectionCard(self._content)
        card.pack(fill="x", pady=(0, 12))
        for label, value in items:
            row = ctk.CTkFrame(card.body, fg_color="transparent")
            row.pack(fill="x", pady=3)
            ctk.CTkLabel(row, text=label, width=180, anchor="w",
                font=ctk.CTkFont(size=12), text_color=MUTED).pack(side="left")
            ctk.CTkLabel(row, text=value, anchor="w",
                font=ctk.CTkFont(size=12), text_color=TEXT_SOFT).pack(side="left")
