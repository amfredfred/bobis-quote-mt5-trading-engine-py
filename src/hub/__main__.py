"""
hub/__main__.py — execution dashboard hub entry point: `python -m src.hub`,
run from the execution-engine repo root.

Owns two servers: an internal one execution-engine instances' forwarders
dial into, and the public one the dashboard connects to. Unlike
signal-engine's hub, this one is duplex — the dashboard can send commands
(cmd.close_trade, cmd.pause, cmd.resume) that get routed back down to the
right broker's instance, since UIBridge's own protocol already works that
way. Never imports MetaTrader5 and never shares state with an instance
beyond relaying frames both directions.
"""
from __future__ import annotations

import asyncio
import logging
import signal as os_signal
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from src.hub.config import HubConfig
from src.hub.state import HubState
from src.hub.internal_server import InternalServer
from src.hub.public_server import PublicServer

logger = logging.getLogger("execution_hub.main")


def _setup_logging(cfg: HubConfig) -> None:
    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    root = logging.getLogger()
    root.setLevel(getattr(logging, cfg.log_level, logging.INFO))
    if root.handlers:
        return

    if sys.stdout:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(fmt)
        root.addHandler(h)

    log_dir = Path.cwd() / cfg.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    fh = RotatingFileHandler(
        log_dir / "execution_hub.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)


async def run(cfg: HubConfig) -> None:
    state = HubState()
    internal = InternalServer(cfg.internal_host, cfg.internal_port, cfg.secret, state)
    public = PublicServer(cfg.public_host, cfg.public_port, state, secret=cfg.secret, max_clients=cfg.max_clients)

    await internal.start()
    await public.start()

    logger.info(
        "Execution Hub up — internal ws://%s:%d  public ws://%s:%d",
        cfg.internal_host, cfg.internal_port, cfg.public_host, cfg.public_port,
    )

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _shutdown() -> None:
        logger.info("Shutdown signal received")
        stop_event.set()

    for sig in (os_signal.SIGINT, os_signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _shutdown)
        except NotImplementedError:
            os_signal.signal(sig, lambda _s, _f: loop.call_soon_threadsafe(_shutdown))

    await stop_event.wait()

    logger.info("Execution Hub stopping")
    await public.stop()
    await internal.stop()
    logger.info("Execution Hub stopped")


def main() -> None:
    cfg = HubConfig.from_env()
    _setup_logging(cfg)
    asyncio.run(run(cfg))


if __name__ == "__main__":
    main()
