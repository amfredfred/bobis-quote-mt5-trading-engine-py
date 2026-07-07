"""
src/config/paths.py — locate config.yaml on disk.

Extracted from the old GUI's ConfigManager (now removed) since the headless
service still needs the same lookup order to support both dev and packaged
(PyInstaller / Windows service) layouts.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_APPNAME = "Apex Quantel"
_PROGRAMDATA_CFG = Path(os.environ.get("PROGRAMDATA", "C:/ProgramData")) / _APPNAME / "config.yaml"


def locate_config_path() -> Path:
    """
    Priority order:
      1. %ProgramData%\\Apex Quantel\\config.yaml  (production install)
      2. Next to the executable / walk up 4 levels (packaged, portable install)
      3. sys._MEIPASS  (PyInstaller bundle)
      4. CWD
      5. Walk up from this file (editable/dev install)
      6. Fallback to ProgramData (created on first save)
    """
    if _PROGRAMDATA_CFG.exists():
        return _PROGRAMDATA_CFG

    exe_dir = Path(sys.executable).parent
    for depth in range(5):
        candidate = exe_dir
        for _ in range(depth):
            candidate = candidate.parent
        cfg = candidate / "config.yaml"
        if cfg.exists():
            return cfg

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        cfg = Path(meipass) / "config.yaml"
        if cfg.exists():
            return cfg

    cfg = Path.cwd() / "config.yaml"
    if cfg.exists():
        return cfg

    for parent in Path(__file__).resolve().parents:
        cfg = parent / "config.yaml"
        if cfg.exists():
            return cfg

    return _PROGRAMDATA_CFG
