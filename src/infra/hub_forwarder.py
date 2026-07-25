"""
src/infra/hub_forwarder.py — relays this instance's local UIBridge to a
dashboard hub (see src/hub), duplex.

Deliberately does not touch ui_bridge.py or any engine internals at all: it
connects to the local UIBridge exactly as a dashboard would (ws://localhost:
<monitoring_port>), so every STATE_SNAPSHOT/METRICS_UPDATE/trade.*/
signal.*/engine.* frame UIBridge already builds is reused verbatim — this
module only relays bytes, it never re-serializes engine state. Commands
routed back down from the hub (cmd.close_trade, cmd.pause, cmd.resume) are
sent down the same local connection, which UIBridge already knows how to
handle from any connected client — no special-casing needed there either.

Two independent legs, each with its own reconnect/backoff, joined by two
in-process queues:
  local UIBridge  →  (queue: local_to_hub)   →  hub
  hub             →  (queue: hub_to_local)   →  local UIBridge

Runs in its own thread with its own asyncio loop, same pattern as
UIBridge._run_loop, started only when config.dashboard_hub_enabled is set —
purely additive, the local UIBridge keeps serving direct connections
unchanged either way.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading

import websockets
import websockets.exceptions

logger = logging.getLogger(__name__)

_RECONNECT_MIN_S = 1.0
_RECONNECT_MAX_S = 30.0
_QUEUE_MAXSIZE = 1000


class HubForwarder:
    def __init__(self, local_url: str, hub_url: str, broker: str, token: str = "") -> None:
        self._local_url = local_url
        self._hub_url = hub_url
        self._broker = broker
        self._token = token
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="hub-forwarder", daemon=True)
        self._thread.start()
        logger.info(
            "HubForwarder starting — broker=%s local=%s hub=%s",
            self._broker, self._local_url, self._hub_url,
        )

    def stop(self) -> None:
        if self._loop and self._stop_event:
            self._loop.call_soon_threadsafe(self._stop_event.set)

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._stop_event = asyncio.Event()
        try:
            loop.run_until_complete(self._main())
        finally:
            loop.close()

    async def _main(self) -> None:
        local_to_hub: asyncio.Queue[str] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        hub_to_local: asyncio.Queue[str] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)

        await asyncio.gather(
            self._run_local_leg(local_to_hub, hub_to_local),
            self._run_hub_leg(local_to_hub, hub_to_local),
            self._stop_event.wait(),  # type: ignore[union-attr]
        )

    # ── Local UIBridge leg (this instance's own local server) ──────────────

    async def _run_local_leg(self, local_to_hub: "asyncio.Queue[str]", hub_to_local: "asyncio.Queue[str]") -> None:
        backoff = _RECONNECT_MIN_S
        while not self._stop_event.is_set():  # type: ignore[union-attr]
            try:
                async with websockets.connect(self._local_url) as ws:
                    logger.info("HubForwarder: connected to local UIBridge (%s)", self._local_url)
                    backoff = _RECONNECT_MIN_S

                    async def _drain_outbound() -> None:
                        while True:
                            frame = await hub_to_local.get()
                            await ws.send(frame)

                    drain_task = asyncio.ensure_future(_drain_outbound())
                    try:
                        async for raw in ws:
                            try:
                                local_to_hub.put_nowait(raw if isinstance(raw, str) else raw.decode())
                            except asyncio.QueueFull:
                                logger.warning("HubForwarder: local->hub queue full, dropping a frame")
                    finally:
                        drain_task.cancel()

            except (websockets.exceptions.ConnectionClosed, OSError) as exc:
                logger.debug("HubForwarder: local UIBridge connection dropped: %s", exc)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("HubForwarder: local leg error: %s", exc)

            if self._stop_event.is_set():  # type: ignore[union-attr]
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _RECONNECT_MAX_S)

    # ── Hub leg (outbound to the dashboard hub) ─────────────────────────────

    async def _run_hub_leg(self, local_to_hub: "asyncio.Queue[str]", hub_to_local: "asyncio.Queue[str]") -> None:
        backoff = _RECONNECT_MIN_S
        while not self._stop_event.is_set():  # type: ignore[union-attr]
            try:
                async with websockets.connect(self._hub_url) as ws:
                    await ws.send(json.dumps({
                        "type": "hello",
                        "payload": {"broker": self._broker, "token": self._token},
                    }))
                    ack = json.loads(await ws.recv())
                    if ack.get("type") != "ack":
                        logger.warning("HubForwarder: hub rejected hello: %s", ack)
                        await asyncio.sleep(backoff)
                        continue

                    logger.info("HubForwarder: connected to hub as broker=%s", self._broker)
                    backoff = _RECONNECT_MIN_S

                    async def _drain_outbound() -> None:
                        while True:
                            frame = await local_to_hub.get()
                            await ws.send(frame)

                    drain_task = asyncio.ensure_future(_drain_outbound())
                    try:
                        async for raw in ws:
                            try:
                                hub_to_local.put_nowait(raw if isinstance(raw, str) else raw.decode())
                            except asyncio.QueueFull:
                                logger.warning("HubForwarder: hub->local queue full, dropping a command")
                    finally:
                        drain_task.cancel()

            except (websockets.exceptions.ConnectionClosed, OSError) as exc:
                logger.debug("HubForwarder: hub connection dropped: %s", exc)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("HubForwarder: hub leg error: %s", exc)

            if self._stop_event.is_set():  # type: ignore[union-attr]
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _RECONNECT_MAX_S)
