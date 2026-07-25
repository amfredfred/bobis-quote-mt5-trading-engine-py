"""
hub/internal_server.py — WebSocket listener execution-engine instances dial
into (via hub_forwarder.py, not directly from UIBridge).

Localhost-only (not publicly reachable). Each instance connects once, sends
a "hello" naming its own broker + a shared secret, then streams the exact
same {type, payload} frames its local UIBridge already sends to any
connected client (STATE_SNAPSHOT, METRICS_UPDATE, trade.*, signal.*,
engine.*) — this module never re-serializes engine state itself, it only
relays. Also stores each connection so public_server can route a dashboard
command back down to the right broker's instance.

This module never touches MT5 or any execution-engine internals — it only
ever accepts a connection a forwarder initiated on its own, same posture as
signal-engine's hub.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import websockets.server
import websockets.exceptions

from src.hub.state import HubState

logger = logging.getLogger(__name__)

_HELLO_TIMEOUT_S = 15.0


class InternalServer:
    def __init__(self, host: str, port: int, secret: str, state: HubState) -> None:
        self._host = host
        self._port = port
        self._secret = secret
        self._state = state
        self._server: Any = None

    async def start(self) -> None:
        self._server = await websockets.server.serve(self._handle, self._host, self._port)
        logger.info("Execution hub internal server listening on ws://%s:%d", self._host, self._port)

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        logger.info("Execution hub internal server stopped")

    # ── Connection handling ──────────────────────────────────────────────────

    async def _handle(self, websocket: Any) -> None:
        broker = await self._await_hello(websocket)
        if broker is None:
            return

        self._state.register(broker, websocket)
        logger.info("Execution hub: broker %s connected", broker)

        try:
            async for raw in websocket:
                self._on_message(broker, raw)
        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception as exc:
            logger.debug("Execution hub: broker %s connection error: %s", broker, exc)
        finally:
            self._state.mark_offline(broker)
            logger.info("Execution hub: broker %s disconnected", broker)

    async def _await_hello(self, websocket: Any) -> str | None:
        try:
            raw = await asyncio.wait_for(websocket.recv(), timeout=_HELLO_TIMEOUT_S)
        except (asyncio.TimeoutError, websockets.exceptions.ConnectionClosed):
            await self._safe_close(websocket, 1008, "hello timeout")
            return None

        try:
            msg = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            await self._safe_close(websocket, 1008, "invalid hello")
            return None

        if msg.get("type") != "hello":
            await self._safe_close(websocket, 1008, "expected hello")
            return None

        payload = msg.get("payload") or {}
        broker = str(payload.get("broker") or "").strip()
        token = str(payload.get("token") or "")

        if not broker:
            await self._safe_close(websocket, 1008, "missing broker")
            return None

        if self._secret and token != self._secret:
            logger.warning("Execution hub: rejected hello from %s — bad token", broker)
            await self._safe_close(websocket, 1008, "invalid token")
            return None

        await websocket.send(json.dumps({"type": "ack", "payload": {}}))
        return broker

    def _on_message(self, broker: str, raw: str | bytes) -> None:
        try:
            frame = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return
        if not isinstance(frame, dict) or "type" not in frame:
            return
        self._state.relay(broker, frame)

    @staticmethod
    async def _safe_close(websocket: Any, code: int, reason: str) -> None:
        try:
            await websocket.close(code, reason)
        except Exception:
            pass
