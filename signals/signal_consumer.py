"""
Connects to the Signal Engine WebSocket, deserialises messages,
validates them, and emits them onto the EventBus.
"""

from __future__ import annotations

import json
import logging
from typing import List

from core.event_bus import EventBus
from core.events import Events
from infrastructure.websocket_client import WebSocketClient
from infrastructure.metrics import metrics
from .signal_types import (
    SIGNAL_TRIGGER_EVENTS,
    SIGNAL_CLOSE_EVENTS,
    is_valid_signal_dict,
)
from .signal_validator import SignalValidator
from interfaces.signal_interface import InboundSignal

logger = logging.getLogger(__name__)


class SignalConsumer:
    """
    Subscribes to the Signal Engine and routes validated signals to the bus.

    Thread model: `WebSocketClient` runs in a daemon thread.
    `on_message` is therefore called from that thread — the EventBus
    dispatches synchronously, so all downstream handlers execute there too.
    If downstream handlers are long-running, consider dispatching to a queue.
    """

    def __init__(
        self,
        event_bus: EventBus,
        validator: SignalValidator,
        ws_url: str,
        ws_secret_key: str,
        symbols: List[str],
    ) -> None:
        self._bus = event_bus
        self._validator = validator
        self._symbols = symbols
        self._ws = WebSocketClient(
            url=ws_url,
            secret_key=ws_secret_key,
            on_message=self._handle_raw,
            on_connected=self._subscribe,
            on_disconnected=self._on_disconnected,
        )

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def start(self) -> None:
        logger.info("SignalConsumer starting", extra={"symbols": self._symbols})
        self._ws.start()

    def stop(self) -> None:
        self._ws.stop()

    # ── Private ───────────────────────────────────────────────────────────

    def _subscribe(self) -> None:
        msg = json.dumps({"action": "subscribe", "symbols": self._symbols})
        self._ws.send(msg)
        logger.info("SignalConsumer subscribed", extra={"symbols": self._symbols})

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

        if not is_valid_signal_dict(payload):
            logger.debug(
                "SignalConsumer: payload is not a signal, skipping event=%s", event
            )
            return

        metrics.increment(f"signal.received.{event}")
        self._process(event, payload)

    def _process(self, event: str, payload: dict) -> None:
        try:
            signal = InboundSignal.from_dict(payload)
        except Exception:
            logger.exception("SignalConsumer: failed to deserialise signal")
            metrics.increment("signal.deserialise_errors")
            return

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
