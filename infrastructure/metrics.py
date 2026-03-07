"""
In-process metrics collector.

Tracks counters and gauges.  Thread-safe via a threading.Lock.
Replace with prometheus_client for production scraping.
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from typing import Dict

logger = logging.getLogger(__name__)


class Metrics:
    def __init__(self) -> None:
        self._lock:     threading.Lock        = threading.Lock()
        self._counters: Dict[str, int]        = defaultdict(int)
        self._gauges:   Dict[str, float]      = {}

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
                "gauges":   dict(self._gauges),
            }

    def log_snapshot(self) -> None:
        logger.info("Metrics snapshot", extra=self.snapshot())


metrics = Metrics()
