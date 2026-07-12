"""is_connected tracks the live socket state, toggled by the on_open/on_close
callbacks websocket-client invokes - not exercised via a real socket."""

from src.infra.websocket import WebSocketClient


def _client() -> WebSocketClient:
    return WebSocketClient(url="ws://localhost:8765", on_message=lambda _msg: None)


def test_is_connected_false_before_any_open() -> None:
    client = _client()

    assert client.is_connected is False


def test_is_connected_true_after_open() -> None:
    client = _client()

    client._handle_open(ws=None)

    assert client.is_connected is True


def test_is_connected_false_after_close() -> None:
    client = _client()
    client._handle_open(ws=None)

    client._handle_close(ws=None, close_status_code=1006, close_msg="abnormal")

    assert client.is_connected is False


def test_on_connected_callback_still_fires() -> None:
    calls: list[str] = []
    client = WebSocketClient(
        url="ws://localhost:8765",
        on_message=lambda _msg: None,
        on_connected=lambda: calls.append("connected"),
        on_disconnected=lambda: calls.append("disconnected"),
    )

    client._handle_open(ws=None)
    client._handle_close(ws=None, close_status_code=None, close_msg=None)

    assert calls == ["connected", "disconnected"]
