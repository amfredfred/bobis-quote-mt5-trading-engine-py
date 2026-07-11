"""
Tests for SignalConsumer against its current API: a plain subscribe-on-connect
WebSocket client with no gateway handshake (no activation, no room TTL, no
heartbeat sequencing, no lifecycle outbox — all of that was gateway-era and
was removed when execution-engine started connecting directly to
signal-engine's own WebSocket server).
"""

import json

from src.core.event_bus import EventBus
from src.core.events import Events
from src.signals.consumer import SignalConsumer
from src.signals.signal_validator import SignalValidator


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def send(self, data: str) -> bool:
        self.sent.append(data)
        return True


def make_consumer(max_age_ms: int = 0) -> tuple[SignalConsumer, FakeWebSocket]:
    consumer = SignalConsumer(
        event_bus=EventBus(),
        validator=SignalValidator(max_age_ms=max_age_ms),
        ws_url="ws://localhost:8765",
        symbols=["XAUUSD"],
    )
    socket = FakeWebSocket()
    consumer._ws = socket  # type: ignore[assignment]
    return consumer, socket


def _signal_payload(
    *, signal_id: str = "sig-001", event: str = "signal.triggered"
) -> dict:
    return {
        "id": signal_id,
        "symbol": "XAUUSD",
        "direction": "LONG",
        "status": "TRIGGERED",
        "entryPrice": 100.0,
        "stopLoss": 99.0,
        "tp1": 102.0,
        "tp2": 104.0,
        "riskRewardRatio": 4.0,
        "riskPips": 1.0,
        "createdAt": 1,
        "htfRange": {
            "rangeHigh": 105.0,
            "rangeLow": 95.0,
            "bosDirection": "BULLISH",
            "timestamp": 1,
            "tpLevel": 104.0,
        },
        "rejectionCandle": {
            "open": 99.5,
            "high": 100.5,
            "low": 99.0,
            "close": 100.0,
            "timestamp": 1,
            "wickRatio": 0.3,
            "pattern": "CRT_BUY",
            "wickTip": 99.0,
        },
    }


def _events(raw_messages: list[str]) -> list[str]:
    return [json.loads(raw)["event"] for raw in raw_messages]


def test_on_connected_sends_a_plain_subscribe_with_no_handshake() -> None:
    consumer, socket = make_consumer()

    consumer._on_connected()

    assert len(socket.sent) == 1
    message = json.loads(socket.sent[0])
    assert message == {"action": "subscribe", "symbols": ["XAUUSD"]}


def test_lifecycle_frames_from_signal_engine_are_ignored() -> None:
    consumer, _ = make_consumer()
    received: list[str] = []
    consumer._bus.on(Events.SIGNAL_RECEIVED, lambda p: received.append("received"))

    for event in ("connected", "subscribed", "unsubscribed"):
        consumer._handle_raw(json.dumps({"event": event, "payload": {}}))

    assert received == []


def test_malformed_json_does_not_raise_or_emit() -> None:
    consumer, _ = make_consumer()
    received: list[str] = []
    consumer._bus.on(Events.SIGNAL_RECEIVED, lambda p: received.append("received"))

    consumer._handle_raw("{not valid json")

    assert received == []


def test_valid_triggered_signal_emits_received_then_triggered() -> None:
    consumer, _ = make_consumer()
    fired: list[tuple[str, object]] = []
    consumer._bus.on(Events.SIGNAL_RECEIVED, lambda p: fired.append(("received", p)))
    consumer._bus.on(Events.SIGNAL_TRIGGERED, lambda p: fired.append(("triggered", p)))

    consumer._handle_raw(
        json.dumps({"event": "signal.triggered", "payload": _signal_payload()})
    )

    assert [name for name, _ in fired] == ["received", "triggered"]
    triggered_signal = fired[1][1]
    assert triggered_signal.id == "sig-001"
    assert triggered_signal.direction.value == "LONG"


def test_duplicate_event_and_signal_id_is_dropped() -> None:
    consumer, _ = make_consumer()
    fired: list[str] = []
    consumer._bus.on(Events.SIGNAL_TRIGGERED, lambda p: fired.append(p.id))

    raw = json.dumps({"event": "signal.triggered", "payload": _signal_payload()})
    consumer._handle_raw(raw)
    consumer._handle_raw(raw)

    assert fired == ["sig-001"]


def test_invalid_signal_emits_rejected_not_received_or_triggered() -> None:
    consumer, _ = make_consumer()
    fired: list[str] = []
    consumer._bus.on(Events.SIGNAL_RECEIVED, lambda p: fired.append("received"))
    consumer._bus.on(Events.SIGNAL_TRIGGERED, lambda p: fired.append("triggered"))
    consumer._bus.on(Events.SIGNAL_REJECTED, lambda p: fired.append("rejected"))

    payload = _signal_payload()
    payload["stopLoss"] = 101.0  # LONG stop must be below entry — invalid

    consumer._handle_raw(json.dumps({"event": "signal.triggered", "payload": payload}))

    assert fired == ["rejected"]


def test_close_event_emits_received_but_not_triggered() -> None:
    consumer, _ = make_consumer()
    fired: list[str] = []
    consumer._bus.on(Events.SIGNAL_RECEIVED, lambda p: fired.append("received"))
    consumer._bus.on(Events.SIGNAL_TRIGGERED, lambda p: fired.append("triggered"))

    payload = _signal_payload(signal_id="sig-002")
    consumer._handle_raw(json.dumps({"event": "signal.tp1_hit", "payload": payload}))

    assert fired == ["received"]
