# engine.spec — PyInstaller build spec for the TradeRelay execution engine.
#
# Build command (from execution-engine/ dir):
#   pyinstaller engine.spec --clean --noconfirm
#
# Or use the build pipeline:
#   powershell -ExecutionPolicy Bypass -File installer\build.ps1 -Clean
#
# Output: dist\TradeRelay-Engine\TradeRelay-Engine.exe  (onedir — see below)
#
# Why --onedir, not --onefile?
#   MetaTrader5 loads the Python-MT5 bridge DLL (MetaTrader5.pyd + Python-3xx.dll)
#   from the directory it was built against at runtime.  --onefile extracts to a
#   random %TEMP% path on every launch, causing DLL resolution to fail silently.
#   --onedir keeps all binaries in a stable dist/ folder so MT5 always finds them.

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
    # The MetaTrader5 package is a thin C-extension wrapper; PyInstaller won't
    # walk it automatically.
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

    # ── websockets (async, used by some signal paths) ────────────────────────
    "websockets",
    "websockets.legacy",
    "websockets.legacy.client",

    # ── PyYAML ───────────────────────────────────────────────────────────────
    "yaml",
    "_yaml",

    # ── python-dotenv ────────────────────────────────────────────────────────
    "dotenv",

    # ── zoneinfo + tzdata ────────────────────────────────────────────────────
    # zoneinfo is stdlib (3.9+) but loads zone files from tzdata at runtime.
    # Windows ships without IANA tzdata; tzdata package provides it.
    "zoneinfo",
    "zoneinfo._czoneinfo",
    "tzdata",
    "tzdata.zoneinfo",

    # ── sqlite3 ──────────────────────────────────────────────────────────────
    # Stdlib; occasionally missed in custom build environments.
    "sqlite3",
    "_sqlite3",

    # ── ssl / certifi ────────────────────────────────────────────────────────
    # Required for wss:// WebSocket connections.
    "ssl",
    "_ssl",
    "certifi",

    # ── numpy (required by MetaTrader5) ─────────────────────────────────────
    # MT5 imports numpy at runtime; PyInstaller misses it because the import
    # is inside a C extension.  Collect all submodules to get _core, linalg, etc.
    "numpy",
    "numpy._core",
    "numpy._core.multiarray",
    "numpy._core._multiarray_umath",
    "numpy.core",
    "numpy.core.multiarray",

    # ── Our own package ──────────────────────────────────────────────────────
    # Force-collect every submodule under src/ so PyInstaller doesn't miss
    # dynamically imported adapters or optional broker paths.
]

# Collect every submodule in our src package — catches dynamic imports
hidden_imports += collect_submodules("src")

# Collect all numpy submodules (covers _core, linalg, fft, random, etc.)
hidden_imports += collect_submodules("numpy")

# ---------------------------------------------------------------------------
# Data files
# ---------------------------------------------------------------------------
datas = [
    # Config template — wizard reads this to merge answers into config.yaml
    ("config.example.yaml", "."),
    # Version file — used by the auto-updater script
    ("version.txt", "."),
]

# Include the full tzdata IANA timezone database (needed on Windows where the
# OS does not ship IANA tz files)
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
        # Development tools — must never appear in the production bundle
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
        # Heavy packages we don't use directly (numpy is kept — MT5 requires it)
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
# EXE — console application
#   console=True is required so NSSM can capture stdout/stderr for log files.
#   icon is optional; Inno Setup will embed its own icon in the installer.
# ---------------------------------------------------------------------------
_icon = None
if sys.platform == "win32":
    import os as _os
    _icon_path = _os.path.join("installer", "assets", "icon.ico")
    if _os.path.exists(_icon_path):
        _icon = _icon_path

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TradeRelay-Engine",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX can corrupt MT5 DLL loading — leave disabled
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_icon,
)

# ---------------------------------------------------------------------------
# COLLECT — produces dist/TradeRelay-Engine/ (onedir layout)
# ---------------------------------------------------------------------------
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="TradeRelay-Engine",
)
