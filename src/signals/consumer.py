"""
Connects directly to the signal engine's own WebSocket server, deserialises
signal events, validates them, and emits them onto the EventBus.
"""

from __future__ import annotations

import json
import logging
import threading
from collections import OrderedDict
from typing import TYPE_CHECKING

from src.core.events import Events
from src.domain.signal_interface import InboundSignal
from src.infra.metrics import metrics
from src.infra.websocket import WebSocketClient
from src.utils.time import now_ms

from .signal_types import (
    SIGNAL_CLOSE_EVENTS,
    SIGNAL_TRIGGER_EVENTS,
    is_valid_signal_dict,
)

if TYPE_CHECKING:
    from src.core.event_bus import EventBus

    from .signal_validator import SignalValidator

logger = logging.getLogger(__name__)


class SignalConsumer:
    """
    Subscribes to symbols on the signal engine's WebSocket server and routes
    validated signals to the event bus.

    Thread model: `WebSocketClient` runs in a daemon thread. `on_message` is
    therefore called from that thread — the EventBus dispatches synchronously,
    so all downstream handlers execute there too. If downstream handlers are
    long-running, consider dispatching to a queue.
    """

    # Maximum number of signal IDs to retain for deduplication.
    # Oldest entries are evicted once this limit is reached.
    _SEEN_IDS_MAX = 10_000

    def __init__(
        self,
        event_bus: EventBus,
        validator: SignalValidator,
        ws_url: str,
        symbols: list[str],
        own_broker: str = "",
    ) -> None:
        self._bus = event_bus
        self._validator = validator
        self._symbols = symbols
        # This engine's own broker identity (mt5.use/MT5_USE) — behind the
        # signal-engine hub, one WS endpoint carries every connected
        # broker's signals, each tagged with `broker`. Only ever act on our
        # own broker's signals; a mismatched one is silently not for this
        # account and must never reach the validator/executor. Empty means
        # unfiltered (single-instance / pre-hub / direct-to-terminal setups
        # keep today's behavior).
        self._own_broker = own_broker.strip().lower()
        # Bounded seen-IDs set for deduplication keyed by (event, signal_id).
        # Using a tuple key prevents lifecycle events (pending → triggered)
        # with the same signal_id from being incorrectly dropped as duplicates.
        self._seen_ids: OrderedDict[tuple[str, str], None] = OrderedDict()
        self._stopped = threading.Event()
        self._ws = WebSocketClient(
            url=ws_url,
            on_message=self._handle_raw,
            on_connected=self._on_connected,
            on_disconnected=self._on_disconnected,
        )

    @property
    def is_connected(self) -> bool:
        """Whether the underlying WebSocket link to the signal engine is up."""
        return self._ws.is_connected

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def start(self) -> None:
        logger.info("SignalConsumer starting", extra={"symbols": self._symbols})
        self._stopped.clear()
        self._ws.start()

    def stop(self) -> None:
        self._stopped.set()
        self._ws.stop()

    # ── Private ───────────────────────────────────────────────────────────

    def _on_connected(self) -> None:
        if self._symbols:
            self._ws.send(
                json.dumps({"action": "subscribe", "symbols": self._symbols})
            )
            logger.info(
                "SignalConsumer subscribed to signal engine",
                extra={"symbols": self._symbols},
            )

    def _on_disconnected(self) -> None:
        logger.warning("Signal Engine disconnected")

    def _handle_raw(self, raw: str) -> None:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("SignalConsumer: JSON parse error", extra={"raw": raw[:200]})
            metrics.increment("signal.parse_errors")
            return

        if not isinstance(parsed, dict):
            return

        event = parsed.get("event", "")
        payload = parsed.get("payload", {})

        if not event or not isinstance(payload, dict):
            return

        # Handshake / lifecycle events from the signal engine's own server —
        # nothing to action, just informational.
        if event in {"connected", "subscribed", "unsubscribed"}:
            logger.debug("SignalConsumer: %s", event, extra={"payload": payload})
            return

        if not is_valid_signal_dict(payload):
            logger.debug(
                "SignalConsumer: payload is not a signal, skipping event=%s", event
            )
            return

        signal_broker = str(payload.get("broker") or "").strip().lower()
        if self._own_broker and signal_broker and signal_broker != self._own_broker:
            logger.debug(
                "SignalConsumer: signal for broker=%s, this engine is broker=%s — ignoring",
                signal_broker, self._own_broker,
            )
            metrics.increment("signal.wrong_broker_dropped")
            return

        metrics.increment(f"signal.received.{event}")
        metrics.set_gauge("signals.last_received_at", float(now_ms()))
        self._process(event, payload)

    def _process(self, event: str, payload: dict) -> None:
        # ── Duplicate (event, signal_id) deduplication ────────────────────
        signal_id = payload.get("id") or payload.get("signal_id") or ""
        if signal_id:
            dedupe_key = (event, signal_id)
            if dedupe_key in self._seen_ids:
                logger.debug(
                    "SignalConsumer: duplicate event/signal_id dropped",
                    extra={"signal_id": signal_id, "event": event},
                )
                metrics.increment("signal.duplicate_dropped")
                return
            # Bounded eviction — remove oldest entry if at capacity
            if len(self._seen_ids) >= self._SEEN_IDS_MAX:
                self._seen_ids.popitem(last=False)
            self._seen_ids[dedupe_key] = None

        try:
            signal = InboundSignal.from_dict(payload)
        except Exception:
            logger.exception("SignalConsumer: failed to deserialise signal")
            metrics.increment("signal.deserialise_errors")
            return

        received_log_at = signal.received_at or now_ms()
        actionable_at = signal.setup_candle_close_at or signal.triggered_at
        logger.info(
            "Signal received",
            extra={
                "signal_id": signal.id,
                "symbol": signal.symbol,
                "setup_candle_open_at": signal.setup_candle_open_at,
                "setup_candle_close_at": signal.setup_candle_close_at,
                "triggered_at": signal.triggered_at,
                "emitted_at": signal.emitted_at,
                "received_at": signal.received_at,
                "age_ms": received_log_at - actionable_at if actionable_at else None,
            },
        )

        result = self._validator.validate(signal)

        if not result.valid:
            logger.warning(
                "SignalConsumer: signal rejected by validator",
                extra={"signal_id": signal.id, "errors": result.errors},
            )
            metrics.increment("signal.validation_failures")
            self._bus.emit(
                Events.SIGNAL_REJECTED, {"signal": signal, "reason": result.errors}
            )
            return

        self._bus.emit(Events.SIGNAL_RECEIVED, {"event": event, "signal": signal})

        if event in SIGNAL_TRIGGER_EVENTS:
            logger.info(
                "SignalConsumer: triggered — forwarding for execution",
                extra={
                    "signal_id": signal.id,
                    "symbol": signal.symbol,
                    "direction": signal.direction.value,
                    "setup_candle_open_at": signal.setup_candle_open_at,
                    "setup_candle_close_at": signal.setup_candle_close_at,
                    "triggered_at": signal.triggered_at,
                    "emitted_at": signal.emitted_at,
                    "received_at": signal.received_at,
                },
            )
            metrics.increment("signal.triggered")
            self._bus.emit(Events.SIGNAL_TRIGGERED, signal)

        elif event in SIGNAL_CLOSE_EVENTS:
            logger.info(
                "SignalConsumer: close event received (informational)",
                extra={"event": event, "signal_id": signal.id},
            )
            metrics.increment(f"signal.close.{event}")
