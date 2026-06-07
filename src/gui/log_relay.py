"""
src/gui/log_relay.py — in-process log capture for the GUI.

A single GuiLogHandler instance is attached to the root logger AFTER
setup_logging() runs (which clears handlers first).  The handler enqueues
log records into a thread-safe deque; the GUI polls it every 100 ms.
"""
from __future__ import annotations

import collections
import logging
import threading

# Thread-safe ring buffer shared across the whole process lifetime.
_buf: collections.deque[dict] = collections.deque(maxlen=500)
_lock = threading.Lock()


class GuiLogHandler(logging.Handler):
    """Captures log records into the shared in-process buffer."""

    def __init__(self) -> None:
        super().__init__()
        fmt = logging.Formatter(
            "%(asctime)s  %(name)-22s  %(message)s", datefmt="%H:%M:%S"
        )
        self.setFormatter(fmt)
        self.setLevel(logging.DEBUG)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = {
                "level": record.levelname,
                "name": record.name,
                "msg": self.format(record),
            }
            with _lock:
                _buf.append(entry)
        except Exception:
            pass


def drain() -> list[dict]:
    """Return and clear all buffered entries (called from GUI main thread)."""
    with _lock:
        entries = list(_buf)
        _buf.clear()
    return entries


# Singleton handler — created once, re-attached after each setup_logging() call.
_handler: GuiLogHandler | None = None


def get_handler() -> GuiLogHandler:
    global _handler
    if _handler is None:
        _handler = GuiLogHandler()
    return _handler
