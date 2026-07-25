"""
hub/state.py — per-broker slot tracking for the execution dashboard hub.

Unlike signal-engine's hub (pure one-way fan-in), this one is duplex: every
slot also holds the live connection object itself, so a command arriving
from a dashboard client can be routed back down to the correct broker's
execution-engine instance. Keyed by broker name throughout — never a bare
"current" singleton — so N independently-connected instances never
overwrite each other, same invariant as the signal-engine hub.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class BrokerSlot:
    broker: str
    connected_at: float
    last_seen: float
    live: bool = True
    connection: Any = None  # the forwarder's websocket, for routing commands down
    last_snapshot: dict | None = None


RelayListener = Callable[[str, dict], None]  # (broker, frame)


class HubState:
    """Owns the per-broker slot dict — the one place the 'always keyed by
    broker, never a singleton' invariant must hold."""

    def __init__(self) -> None:
        self._slots: dict[str, BrokerSlot] = {}
        self._listeners: list[RelayListener] = []

    def on_relay(self, listener: RelayListener) -> None:
        self._listeners.append(listener)

    def register(self, broker: str, connection: Any) -> BrokerSlot:
        now = time.time()
        slot = self._slots.get(broker)
        if slot is None:
            slot = BrokerSlot(broker=broker, connected_at=now, last_seen=now, connection=connection)
            self._slots[broker] = slot
        else:
            slot.connected_at = now
            slot.last_seen = now
            slot.live = True
            slot.connection = connection
        return slot

    def mark_offline(self, broker: str) -> None:
        slot = self._slots.get(broker)
        if slot is not None:
            slot.live = False
            slot.connection = None

    def relay(self, broker: str, frame: dict) -> None:
        """A frame forwarded from that broker's execution-engine instance
        (STATE_SNAPSHOT, METRICS_UPDATE, trade.*, signal.*, engine.*)."""
        slot = self._slots.get(broker)
        if slot is None:
            return
        slot.last_seen = time.time()
        if frame.get("type") == "STATE_SNAPSHOT":
            slot.last_snapshot = frame.get("payload")
        for listener in self._listeners:
            listener(broker, frame)

    def connection_for(self, broker: str) -> Any:
        slot = self._slots.get(broker)
        return slot.connection if slot and slot.live else None

    def last_snapshot_for(self, broker: str) -> dict | None:
        slot = self._slots.get(broker)
        return slot.last_snapshot if slot else None

    def snapshot(self) -> dict[str, dict]:
        return {
            broker: {"live": slot.live, "connectedAt": slot.connected_at, "lastSeen": slot.last_seen}
            for broker, slot in self._slots.items()
        }
