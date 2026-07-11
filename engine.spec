# engine.spec — PyInstaller build spec for the Apex Quantel execution engine.
#
# Build command (from execution-engine/ dir):
#   pyinstaller engine.spec --clean --noconfirm
#
# Output: dist\apex-quant-trader-agent\apex-quant-trader-agent.exe  (onedir — see below)
#
# Why --onedir, not --onefile?
#   MetaTrader5 loads the Python-MT5 bridge DLL (MetaTrader5.pyd + Python-3xx.dll)
#   from the directory it was built against at runtime.  --onefile extracts to a
#   random %TEMP% path on every launch, causing DLL resolution to fail silently.
#   --onedir keeps all binaries in a stable dist/ folder so MT5 always finds them.
#
# This is a headless-only build - there is no GUI. src/__main__.py runs the
# service directly; it is installed as a Windows Task Scheduler task (see
# install.ps1), not a service, since MT5 needs an interactive desktop session.

import os
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules  # noqa: F401

block_cipher = None

# ---------------------------------------------------------------------------
# Hidden imports — packages whose sub-modules aren't auto-discovered because
# they rely on runtime __import__, C-extension entry points, or lazy loading.
# ---------------------------------------------------------------------------
hidden_imports = [
    # ── MetaTrader5 ──────────────────────────────────────────────────────────
    "MetaTrader5",

    # ── websocket-client ────────────────────────────────────────────────────
    "websocket",
    "websocket._app",
    "websocket._abnf",
    "websocket._core",
    "websocket._exceptions",
    "websocket._handshake",
    "websocket._http",
    "websocket._logging",
    "websocket._socket",
    "websocket._ssl_compat",
    "websocket._utils",

    # ── websockets (async, UIBridge server) ─────────────────────────────────
    "websockets",
    "websockets.legacy",
    "websockets.legacy.client",
    "websockets.legacy.server",

    # ── PyYAML ───────────────────────────────────────────────────────────────
    "yaml",
    "_yaml",

    # ── python-dotenv ────────────────────────────────────────────────────────
    "dotenv",

    # ── zoneinfo + tzdata ────────────────────────────────────────────────────
    "zoneinfo",
    "zoneinfo._czoneinfo",
    "tzdata",
    "tzdata.zoneinfo",

    # ── sqlite3 ──────────────────────────────────────────────────────────────
    "sqlite3",
    "_sqlite3",

    # ── ssl / certifi ────────────────────────────────────────────────────────
    "ssl",
    "_ssl",
    "certifi",

    # ── psutil (CPU/memory reporting) ───────────────────────────────────────
    "psutil",

    # ── numpy (required by MetaTrader5) ─────────────────────────────────────
    "numpy",
    "numpy._core",
    "numpy._core.multiarray",
    "numpy._core._multiarray_umath",
    "numpy.core",
    "numpy.core.multiarray",
]

# Collect every submodule in our src package
hidden_imports += collect_submodules("src")

# Collect all numpy submodules (covers _core, linalg, fft, random, etc.)
hidden_imports += collect_submodules("numpy")

# ---------------------------------------------------------------------------
# Data files
# ---------------------------------------------------------------------------
datas = [
    # Version file — used by the auto-updater script and UIBridge's reported version
    ("version.txt", "."),
    # Default config — placed next to the exe so it's found on first launch
    ("config.yaml",  "."),
]

# Include the full tzdata IANA timezone database
datas += collect_data_files("tzdata")

# numpy data files (.pyd C extensions, .pyi stubs, etc.)
datas += collect_data_files("numpy")

# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
# sys.base_prefix = the base Python install dir (e.g. C:\Python312).
# Adding it to pathex lets PyInstaller's bootloader find python312.dll when
# running from a venv, where the DLL lives in the base install rather than
# the venv Scripts\ folder.
_base_python_dir = sys.base_prefix

a = Analysis(
    ["src/__main__.py"],
    pathex=[".", _base_python_dir],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Development tools
        "pytest",
        "pytest_asyncio",
        "ruff",
        "mypy",
        "pre_commit",
        "pip",
        "setuptools",
        "wheel",
        "hatch",
        "hatchling",
        # No GUI - headless service only
        "tkinter",
        "customtkinter",
        "darkdetect",
        "PIL",
        # Heavy unused packages (numpy is kept — MT5 requires it)
        "pandas",
        "matplotlib",
        "scipy",
        "IPython",
        "notebook",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ---------------------------------------------------------------------------
# EXE
#   console=False  — no visible window; this runs invisibly in the background
#                    as a Task Scheduler task (see install.ps1).
# ---------------------------------------------------------------------------
_icon = None
if sys.platform == "win32":
    _icon_path = os.path.join("installer", "assets", "icon.ico")
    if os.path.exists(_icon_path):
        _icon = _icon_path

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="apex-quant-trader-agent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX can corrupt MT5 DLL loading — leave disabled
    console=False,
    uac_admin=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_icon,
)

# ---------------------------------------------------------------------------
# COLLECT — produces dist/apex-quant-trader-agent/ (onedir layout)
# ---------------------------------------------------------------------------
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="apex-quant-trader-agent",
)
