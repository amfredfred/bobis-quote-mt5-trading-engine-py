"""
In-process metrics collector.

Tracks counters and gauges. Thread-safe via a threading.Lock.
Persists to SQLite every FLUSH_INTERVAL_SEC seconds and restores
on startup so counters survive engine restarts.

Replace with prometheus_client for production scraping.
"""

from __future__ import annotations

import logging
import os
import threading
from collections import defaultdict, deque
from typing import TYPE_CHECKING, Dict, Optional

if TYPE_CHECKING:
    from src.infra.db import Database

logger = logging.getLogger(__name__)

FLUSH_INTERVAL_SEC = 30
REJECTION_LOG_MAXLEN = 200


def get_memory_mb() -> float:
    """Resident memory of this process, in MB. 0.0 if unavailable."""
    try:
        import psutil

        return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
    except ImportError:
        return 0.0


def get_cpu_percent() -> Optional[float]:
    """Process CPU usage since the last call. None if psutil isn't installed.

    The non-blocking cpu_percent() reads 0.0 on its very first call per
    process (needs a prior sample to diff against) - primed once at import
    time below so the first real snapshot already has a meaningful value.
    """
    try:
        import psutil

        return psutil.Process(os.getpid()).cpu_percent(interval=None)
    except ImportError:
        return None


get_cpu_percent()  # prime the baseline sample


class Metrics:
    def __init__(self) -> None:
        self._lock: threading.Lock = threading.Lock()
        self._counters: Dict[str, int] = defaultdict(int)
        self._gauges: Dict[str, float] = {}
        self._rejections: "deque[dict]" = deque(maxlen=REJECTION_LOG_MAXLEN)
        self._db: Optional["Database"] = None
        self._flush_timer: Optional[threading.Timer] = None

    # ── DB wiring ─────────────────────────────────────────────────────────

    def init_db(self, db: "Database") -> None:
        """
        Call once from bootstrap after Database.init().
        Restores persisted counters/gauges then starts the periodic flush.
        """
        self._db = db
        self._restore()
        self._schedule_flush()
        logger.info("Metrics DB persistence enabled")

    def _restore(self) -> None:
        if not self._db:
            return
        try:
            counters, gauges = self._db.load_metrics()
            with self._lock:
                for k, v in counters.items():
                    self._counters[k] = v
                self._gauges.update(gauges)
            logger.info(
                "Metrics restored from DB",
                extra={"counters": len(counters), "gauges": len(gauges)},
            )
        except Exception:
            logger.exception("Metrics: failed to restore from DB")

    def flush(self) -> None:
        """Persist current snapshot to DB. Called periodically and on shutdown."""
        if not self._db:
            return
        try:
            snap = self.snapshot()
            self._db.save_metrics(snap["counters"], snap["gauges"])
        except Exception:
            logger.exception("Metrics: failed to flush to DB")

    def _schedule_flush(self) -> None:
        self._flush_timer = threading.Timer(FLUSH_INTERVAL_SEC, self._tick)
        self._flush_timer.daemon = True
        self._flush_timer.start()

    def _tick(self) -> None:
        self.flush()
        self._schedule_flush()  # reschedule

    def stop(self) -> None:
        """Cancel the flush timer and do a final flush. Call from bootstrap shutdown."""
        if self._flush_timer:
            self._flush_timer.cancel()
        self.flush()

    # ── Core API (unchanged) ──────────────────────────────────────────────

    def increment(self, name: str, by: int = 1) -> None:
        with self._lock:
            self._counters[name] += by

    def set_gauge(self, name: str, value: float) -> None:
        with self._lock:
            self._gauges[name] = value

    def counter(self, name: str) -> int:
        with self._lock:
            return self._counters[name]

    def gauge(self, name: str) -> float:
        with self._lock:
            return self._gauges.get(name, 0.0)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
            }

    # ── Risk rejections (in-memory ring buffer, not persisted) ─────────────
    #
    # Every "Risk rejected" log line already carries the forensic fields
    # needed to diagnose an RRR collapse (both R:Rs, live bid/ask, fill
    # price) but was only ever grep-able from raw logs. This makes that same
    # data queryable in-process for a dashboard or ad-hoc inspection.

    def record_rejection(self, record: dict) -> None:
        with self._lock:
            self._rejections.append(record)

    def recent_rejections(self, rule: Optional[str] = None, limit: int = 50) -> list:
        with self._lock:
            items = list(self._rejections)
        if rule:
            items = [r for r in items if r.get("rule") == rule]
        return items[-limit:]

    def log_snapshot(self) -> None:
        logger.info("Metrics snapshot", extra=self.snapshot())


metrics = Metrics()









