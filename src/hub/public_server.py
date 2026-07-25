"""
hub/public_server.py — public-facing WebSocket server dashboards connect to.

Same default port (8080) execution-engine's UIBridge already used for a
single instance, so existing dashboard config barely changes — just point
NEXT_PUBLIC_EXECUTION_ENGINE_WS_URL at this hub instead of one engine's own
port. Every relayed frame gets a "broker" field stamped on (authoritative,
from the connection identity — see internal_server.py), and every frame a
dashboard sends back (commands: cmd.close_trade, cmd.pause, cmd.resume)
must include which broker it targets, routed down via HubState's stored
connection for that broker. This is the one duplex difference from
signal-engine's pure fan-in hub — UIBridge's own command protocol is
bidirectional, so this has to be too.
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


class PublicServer:
    def __init__(self, host: str, port: int, state: HubState, secret: str = "", max_clients: int = 0) -> None:
        self._host = host
        self._port = port
        self._state = state
        self._secret = secret
        self._max_clients = max_clients
        self._clients: set[Any] = set()
        self._server: Any = None

        state.on_relay(self._on_relay)

    async def start(self) -> None:
        self._server = await websockets.server.serve(self._handle, self._host, self._port)
        logger.info("Execution hub public server listening on ws://%s:%d", self._host, self._port)

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        logger.info("Execution hub public server stopped")

    # ── HubState callback (broker → dashboard) ──────────────────────────────

    def _on_relay(self, broker: str, frame: dict) -> None:
        if not self._clients:
            return
        tagged = {**frame, "broker": broker}
        asyncio.get_running_loop().create_task(self._broadcast(tagged))

    async def _broadcast(self, frame: dict) -> None:
        message = json.dumps(frame, default=str)
        dead: set[Any] = set()
        for client in list(self._clients):
            try:
                await client.send(message)
            except Exception:
                dead.add(client)
        self._clients -= dead

    # ── Connection handling (dashboard side) ────────────────────────────────

    async def _handle(self, websocket: Any) -> None:
        if self._secret and _extract_token(websocket) != self._secret:
            logger.warning("Execution hub public server: rejected connection — bad or missing token")
            await websocket.close(1008, "invalid token")
            return

        if self._max_clients and len(self._clients) >= self._max_clients:
            await websocket.close(1013, "server at capacity")
            return

        self._clients.add(websocket)
        logger.info("Execution hub public server: dashboard client connected (%d total)", len(self._clients))

        # Replay each currently-live broker's last STATE_SNAPSHOT immediately
        # — a forwarder only sends STATE_SNAPSHOT once, at its own connect
        # time, so a dashboard that joins later would otherwise wait for the
        # next periodic METRICS_UPDATE before showing anything.
        for broker, slot_info in self._state.snapshot().items():
            if not slot_info.get("live"):
                continue
            last = self._state.last_snapshot_for(broker)
            if last is not None:
                try:
                    await websocket.send(json.dumps(
                        {"type": "STATE_SNAPSHOT", "payload": last, "broker": broker}, default=str,
                    ))
                except Exception:
                    pass

        try:
            async for raw in websocket:
                await self._on_command(raw)
        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception as exc:
            logger.debug("Execution hub public server: client error: %s", exc)
        finally:
            self._clients.discard(websocket)
            logger.info("Execution hub public server: dashboard client disconnected (%d total)", len(self._clients))

    # ── Dashboard → broker command routing ──────────────────────────────────

    async def _on_command(self, raw: str | bytes) -> None:
        try:
            msg = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return
        if not isinstance(msg, dict):
            return

        broker = str(msg.get("broker") or "").strip()
        connection = self._state.connection_for(broker) if broker else None
        if connection is None:
            logger.warning(
                "Execution hub: command %r targets broker=%r, which isn't connected — dropped",
                msg.get("type"), broker,
            )
            return

        forward = {k: v for k, v in msg.items() if k != "broker"}
        try:
            await connection.send(json.dumps(forward, default=str))
        except Exception as exc:
            logger.warning("Execution hub: failed to route command to broker=%s: %s", broker, exc)


def _extract_token(websocket: Any) -> str:
    try:
        path = websocket.request.path
    except AttributeError:
        path = getattr(websocket, "path", "/")
    try:
        from urllib.parse import parse_qs, urlparse
        qs = parse_qs(urlparse(path).query)
        return qs.get("token", [""])[0]
    except Exception:
        return ""
