import json

from src.core.event_bus import EventBus
from src.signals.consumer import SignalConsumer
from src.signals.signal_validator import SignalValidator


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def send(self, data: str) -> bool:
        self.sent.append(data)
        return True


def make_consumer() -> tuple[SignalConsumer, FakeWebSocket]:
    consumer = SignalConsumer(
        event_bus=EventBus(),
        validator=SignalValidator(),
        ws_url="ws://localhost:4000/engine",
        activation_key="test-activation-key-001",
        symbols=["XAUUSD"],
        engine_id="execution-test-001",
        engine_version="0.1.0",
        room_ttl_seconds=60,
        account_login="106189638",
    )
    socket = FakeWebSocket()
    consumer._ws = socket  # type: ignore[assignment]
    return consumer, socket


def control(event: str, data: dict) -> str:
    return json.dumps({"event": event, "data": data})


def test_connected_waits_for_activation_before_joining_symbol_rooms() -> None:
    consumer, socket = make_consumer()

    consumer._on_connected()

    messages = [json.loads(raw) for raw in socket.sent]
    assert [message["event"] for message in messages] == ["engine.hello"]
    assert messages[0]["data"]["payload"]["engine_id"] == "execution-test-001"

    consumer._handle_raw(
        control("protocol.accepted", {"message_id": consumer._hello_message_id})
    )
    activation = json.loads(socket.sent[1])
    assert activation["event"] == "activation.request"
    assert activation["data"]["payload"]["activation_key"] == "test-activation-key-001"

    consumer._handle_raw(
        control(
            "activation.accepted",
            {
                "message_id": consumer._activation_message_id,
                "engine_id": "execution-test-001",
                "symbols": ["XAUUSD"],
            },
        )
    )
    subscription = json.loads(socket.sent[2])
    assert subscription["event"] == "room.subscribe"
    assert subscription["data"]["payload"] == {
        "engine_id": "execution-test-001",
        "symbols": ["XAUUSD"],
        "ttl_seconds": 60,
    }


def test_activation_rejection_does_not_join_rooms() -> None:
    consumer, socket = make_consumer()
    consumer._on_connected()
    consumer._handle_raw(
        control("protocol.accepted", {"message_id": consumer._hello_message_id})
    )

    consumer._handle_raw(
        control(
            "protocol.rejected",
            {
                "message_id": consumer._activation_message_id,
                "errors": ["invalid activation key"],
            },
        )
    )

    assert [json.loads(raw)["event"] for raw in socket.sent] == [
        "engine.hello",
        "activation.request",
    ]
    assert not consumer._activated.is_set()


def test_room_refresh_resends_subscription() -> None:
    consumer, socket = make_consumer()

    consumer._subscribe()
    consumer._subscribe()

    messages = [json.loads(raw) for raw in socket.sent]
    assert [message["event"] for message in messages] == [
        "room.subscribe",
        "room.subscribe",
    ]
    assert messages[0]["data"]["message_id"] != messages[1]["data"]["message_id"]


def test_heartbeat_increments_sequence() -> None:
    consumer, socket = make_consumer()

    consumer._heartbeat()
    consumer._heartbeat()

    messages = [json.loads(raw) for raw in socket.sent]
    assert [message["event"] for message in messages] == [
        "engine.heartbeat",
        "engine.heartbeat",
    ]
    assert [message["data"]["payload"]["sequence"] for message in messages] == [1, 2]


def test_lifecycle_report_contains_only_execution_reference_data() -> None:
    consumer, _ = make_consumer()

    consumer._queue_lifecycle("attempted", "signal-test-001")

    event, report = consumer._lifecycle_queue.get_nowait()
    assert event == "execution.lifecycle"
    assert report["stage"] == "attempted"
    assert report["signal_id"] == "signal-test-001"
    assert report["account_login"] == "106189638"
    assert "symbol" not in report
    assert "signal" not in report
