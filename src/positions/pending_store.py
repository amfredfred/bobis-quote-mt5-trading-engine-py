"""
Thread-safe in-memory store for resting limit orders awaiting a fill.

Separate from PositionStore by design — a pending order and an open
position are different lifecycles (one hasn't risked anything yet, the
other has), not the same thing in two states. MT5 is the source of truth
for whether a ticket is still resting, same convention PositionStore uses
for open positions.

Known gap: no startup hydration from broker yet, unlike PositionStore's
hydrate_from_broker(). If the engine restarts while orders are resting,
they're orphaned from this store's point of view (MT5 still holds/expires
them correctly server-side, but PendingOrderManager won't know to hand off
a fill to PositionStore for one it never re-discovered). Acceptable for now
since pure_crt (the only limit-order strategy) isn't live yet; needs
addressing before this goes live.
"""

from __future__ import annotations

import copy
import threading
from dataclasses import dataclass
from typing import Dict, List, Optional

from src.domain.trade import TradePlan


@dataclass
class PendingOrderRecord:
    ticket: int
    plan: TradePlan
    placed_at: int      # Unix ms
    expiry_at: int       # Unix ms — our own safety-net check; MT5 also
                          # auto-cancels server-side via the order's own
                          # `expiration` field (see Mt5Orders.open_limit_order)


class PendingOrderStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: Dict[int, PendingOrderRecord] = {}  # ticket -> record

    def add(self, record: PendingOrderRecord) -> None:
        with self._lock:
            self._records[record.ticket] = copy.deepcopy(record)

    def remove(self, ticket: int) -> None:
        with self._lock:
            self._records.pop(ticket, None)

    def get(self, ticket: int) -> Optional[PendingOrderRecord]:
        with self._lock:
            r = self._records.get(ticket)
            return copy.deepcopy(r) if r else None

    def get_all(self) -> List[PendingOrderRecord]:
        with self._lock:
            return [copy.deepcopy(r) for r in self._records.values()]

    def size(self) -> int:
        with self._lock:
            return len(self._records)
