"""
Connects to the TradeRelay Gateway WebSocket, deserialises messages,
validates them, and emits them onto the EventBus.
"""

from __future__ import annotations

import json
import logging
import platform
import queue
import socket
import threading
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Callable
from uuid import uuid4

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
    Joins Gateway symbol rooms and routes validated signals to the bus.

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
        activation_key: str,
        symbols: list[str],
        engine_id: str,
        engine_version: str,
        room_ttl_seconds: int,
        account_login: str,
    ) -> None:
        self._bus = event_bus
        self._validator = validator
        self._symbols = symbols
        self._activation_key = activation_key
        self._engine_id = engine_id
        self._engine_version = engine_version
        self._room_ttl_seconds = room_ttl_seconds
        self._account_login = account_login
        self._started_at = self._utc_now()
        self._stopped = threading.Event()
        self._activated = threading.Event()
        self._refresh_thread: threading.Thread | None = None
        self._heartbeat_thread: threading.Thread | None = None
        self._lifecycle_thread: threading.Thread | None = None
        self._lifecycle_queue: queue.Queue[tuple[str, dict]] = queue.Queue(maxsize=1000)
        self._heartbeat_sequence = 0
        self._metrics_subscribed = threading.Event()
        self._snapshot_provider: Callable[[], dict] | None = None
        self._metrics_thread: threading.Thread | None = None
        self._hello_message_id: str | None = None
        self._activation_message_id: str | None = None
        self._ws = WebSocketClient(
            url=ws_url,
            on_message=self._handle_raw,
            on_connected=self._on_connected,
            on_disconnected=self._on_disconnected,
        )

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def start(self) -> None:
        logger.info("SignalConsumer starting", extra={"symbols": self._symbols})
        self._stopped.clear()
        self._refresh_thread = threading.Thread(
            target=self._refresh_rooms,
            name="gateway-room-refresh",
            daemon=True,
        )
        self._refresh_thread.start()
        self._heartbeat_thread = threading.Thread(
            target=self._send_heartbeats,
            name="gateway-heartbeat",
            daemon=True,
        )
        self._heartbeat_thread.start()
        self._lifecycle_thread = threading.Thread(
            target=self._send_lifecycle_reports,
            name="gateway-lifecycle-reporter",
            daemon=True,
        )
        self._lifecycle_thread.start()
        self._metrics_thread = threading.Thread(
            target=self._send_metrics_snapshots,
            name="gateway-metrics-reporter",
            daemon=True,
        )
        self._metrics_thread.start()
        self._ws.start()

    def stop(self) -> None:
        self._stopped.set()
        self._ws.stop()

    def set_snapshot_provider(self, provider: Callable[[], dict]) -> None:
        self._snapshot_provider = provider

    # ── Private ───────────────────────────────────────────────────────────

    def _on_connected(self) -> None:
        self._activated.clear()
        self._hello_message_id = self._send(
            "engine.hello",
            {
                "engine_id": self._engine_id,
                "engine_version": self._engine_version,
                "protocol_versions": ["1.0"],
                "started_at": self._started_at,
                "accounts": [],
            },
        )

    def _activate(self) -> None:
        architecture = "arm64" if platform.machine().lower() == "arm64" else "x64"
        self._activation_message_id = self._send(
            "activation.request",
            {
                "activation_key": self._activation_key,
                "device_name": socket.gethostname(),
                "engine_version": self._engine_version,
                "platform": {"os": "windows", "architecture": architecture},
                "mt5_accounts": [],
            },
        )

    def _subscribe(self) -> None:
        sent = self._send(
            "room.subscribe",
            {
                "engine_id": self._engine_id,
                "symbols": self._symbols,
                "ttl_seconds": self._room_ttl_seconds,
            },
        )
        if sent:
            logger.info(
                "SignalConsumer joined Gateway rooms",
                extra={
                    "engine_id": self._engine_id,
                    "symbols": self._symbols,
                    "ttl_seconds": self._room_ttl_seconds,
                },
            )

    def _on_disconnected(self) -> None:
        self._activated.clear()
        self._metrics_subscribed.clear()
        logger.warning("TradeRelay Gateway disconnected")

    def _refresh_rooms(self) -> None:
        refresh_interval = max(15.0, self._room_ttl_seconds / 2)
        while not self._stopped.wait(refresh_interval):
            if self._activated.is_set():
                self._subscribe()

    def _send_heartbeats(self) -> None:
        while not self._stopped.wait(30.0):
            if self._activated.is_set():
                self._heartbeat()

    def _send_metrics_snapshots(self) -> None:
        while not self._stopped.wait(1.5):
            if (
                self._activated.is_set()
                and self._metrics_subscribed.is_set()
                and self._snapshot_provider
            ):
                self._send("execution.metrics.snapshot", self._snapshot_provider())

    def _heartbeat(self) -> None:
        self._heartbeat_sequence += 1
        self._send(
            "engine.heartbeat",
            {
                "engine_id": self._engine_id,
                "status": "running",
                "sequence": self._heartbeat_sequence,
                "observed_at": self._utc_now(),
            },
        )

    def report_event(self, event: str, payload: object) -> None:
        stage: str | None = None
        signal_id: str | None = None
        reason: str | None = None
        trade_id: str | None = None
        broker_ticket: str | None = None

        if event == Events.SIGNAL_RECEIVED:
            if payload["event"] in SIGNAL_TRIGGER_EVENTS:  # type: ignore[index]
                stage = "accepted"
                signal_id = payload["signal"].id  # type: ignore[index]
        elif event in {Events.SIGNAL_REJECTED, Events.RISK_REJECTED}:
            stage = "rejected"
            signal_id = payload["signal"].id  # type: ignore[index]
            reason = self._reason(payload["reason"])  # type: ignore[index]
        elif event == Events.EXECUTION_ATTEMPTED:
            stage = "attempted"
            signal_id = payload.id  # type: ignore[union-attr]
        elif event == Events.TRADE_OPENED:
            stage = "opened"
            signal_id = payload.signal_id  # type: ignore[union-attr]
            trade_id = payload.id  # type: ignore[union-attr]
            ticket = payload.entry_ticket  # type: ignore[union-attr]
            broker_ticket = str(ticket) if ticket is not None else None
        elif event == Events.TRADE_ERROR:
            stage = "failed"
            signal_id = payload["signal"].id  # type: ignore[index]
            reason = self._reason(payload["reason"])  # type: ignore[index]

        if stage and signal_id:
            self._queue_lifecycle(stage, signal_id, reason, trade_id, broker_ticket)

    def _queue_lifecycle(
        self,
        stage: str,
        signal_id: str,
        reason: str | None = None,
        trade_id: str | None = None,
        broker_ticket: str | None = None,
    ) -> None:
        report = {
            "engine_id": self._engine_id,
            "signal_id": signal_id,
            "account_login": self._account_login,
            "stage": stage,
            "observed_at": self._utc_now(),
        }
        if reason:
            report["reason"] = reason[:1000]
        if trade_id:
            report["trade_id"] = trade_id
        if broker_ticket:
            report["broker_ticket"] = broker_ticket
        try:
            self._lifecycle_queue.put_nowait(("execution.lifecycle", report))
        except queue.Full:
            logger.error(
                "Execution lifecycle queue full; report dropped",
                extra={"stage": stage, "signal_id": signal_id},
            )

    def _send_lifecycle_reports(self) -> None:
        while not self._stopped.is_set():
            if not self._activated.wait(timeout=1.0):
                continue
            try:
                event, report = self._lifecycle_queue.get(timeout=1.0)
            except queue.Empty:
                continue
            if not self._send(event, report):
                logger.warning(
                    "Execution lifecycle report could not be sent",
                    extra={"stage": report["stage"], "signal_id": report["signal_id"]},
                )
            self._lifecycle_queue.task_done()

    @staticmethod
    def _reason(value: object) -> str:
        if isinstance(value, list):
            return "; ".join(str(item) for item in value)
        return str(value)

    def _send(self, event: str, payload: dict) -> str | None:
        message_id = str(uuid4())
        message = {
            "event": event,
            "data": {
                "protocol_version": "1.0",
                "message_id": message_id,
                "sent_at": self._utc_now(),
                "payload": payload,
            },
        }
        if self._ws.send(json.dumps(message, separators=(",", ":"))):
            return message_id
        return None

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(UTC).isoformat().replace("+00:00", "Z")

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
        data = parsed.get("data", {})
        if event and isinstance(data, dict) and self._handle_control(event, data):
            return

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

    def _handle_control(self, event: str, data: dict) -> bool:
        message_id = data.get("message_id")

        if event == "protocol.accepted" and message_id == self._hello_message_id:
            self._activate()
            return True

        if event == "activation.accepted":
            if message_id != self._activation_message_id:
                logger.warning("Ignoring unexpected Gateway activation response")
                return True
            if data.get("engine_id") != self._engine_id:
                logger.error("Gateway activation response engine_id mismatch")
                return True
            self._activated.set()
            self._subscribe()
            logger.info(
                "TradeRelay activation accepted",
                extra={"engine_id": self._engine_id, "symbols": data.get("symbols", [])},
            )
            return True

        if event == "protocol.rejected":
            if message_id in {self._hello_message_id, self._activation_message_id}:
                self._activated.clear()
                logger.error(
                    "TradeRelay Gateway rejected connection setup",
                    extra={"message_id": message_id, "errors": data.get("errors", [])},
                )
            return True

        if event == "execution.metrics.subscribe":
            self._metrics_subscribed.set()
            if self._snapshot_provider:
                self._send("execution.metrics.snapshot", self._snapshot_provider())
            return True

        if event == "execution.metrics.unsubscribe":
            self._metrics_subscribed.clear()
            return True

        return event in {
            "protocol.accepted",
            "activation.accepted",
            "execution.metrics.subscribe",
            "execution.metrics.unsubscribe",
        }

    def _process(self, event: str, payload: dict) -> None:
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
        if event in SIGNAL_TRIGGER_EVENTS:
            self._queue_lifecycle("received", signal.id)

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
