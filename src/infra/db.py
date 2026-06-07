"""
SQLite persistence layer.

Single file database at <storage_path>/engine.db

Tables:
    trades          — full trade lifecycle, open and closed
    signals         — every inbound signal received
    metrics_counters — persisted counter values (survive restarts)
    metrics_gauges   — persisted gauge values (survive restarts)

All writes are atomic. The DB is created and migrated on init().
Thread-safe — SQLite WAL mode + per-call connections via check_same_thread=False.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, storage_path: str) -> None:
        self._path = str(Path(storage_path) / "engine.db")
        self._lock = threading.Lock()

    def init(self) -> None:
        """Create tables if they don't exist. Safe to call on every startup."""
        with self._connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA foreign_keys=ON;

                CREATE TABLE IF NOT EXISTS trades (
                    id              TEXT PRIMARY KEY,
                    signal_id       TEXT,
                    symbol          TEXT NOT NULL,
                    side            TEXT NOT NULL,
                    status          TEXT NOT NULL,
                    entry_ticket    INTEGER,
                    entry_price     REAL,
                    entry_lots      REAL,
                    current_lots    REAL,
                    stop_loss       REAL,
                    tp1             REAL,
                    tp2             REAL,
                    tp1_hit         INTEGER DEFAULT 0,
                    tp1_hit_at      INTEGER,
                    tp2_hit         INTEGER DEFAULT 0,
                    tp2_hit_at      INTEGER,
                    sl_hit          INTEGER DEFAULT 0,
                    sl_hit_at       INTEGER,
                    close_reason    TEXT,
                    close_price     REAL,
                    realized_pnl    REAL,
                    realized_rr     REAL,
                    plan_json       TEXT,
                    opened_at       INTEGER,
                    closed_at       INTEGER,
                    created_at      INTEGER,
                    updated_at      INTEGER
                );

                CREATE INDEX IF NOT EXISTS idx_trades_status
                    ON trades(status);
                CREATE INDEX IF NOT EXISTS idx_trades_symbol
                    ON trades(symbol);
                CREATE INDEX IF NOT EXISTS idx_trades_signal_id
                    ON trades(signal_id);
                CREATE INDEX IF NOT EXISTS idx_trades_ticket
                    ON trades(entry_ticket);

                CREATE TABLE IF NOT EXISTS signals (
                    id              TEXT PRIMARY KEY,
                    symbol          TEXT NOT NULL,
                    direction       TEXT NOT NULL,
                    status          TEXT NOT NULL,
                    entry_price     REAL,
                    stop_loss       REAL,
                    tp1             REAL,
                    tp2             REAL,
                    risk_reward     REAL,
                    risk_pips       REAL,
                    pattern         TEXT,
                    wick_ratio      REAL,
                    raw_json        TEXT,
                    received_at     INTEGER,
                    triggered_at    INTEGER,
                    outcome         TEXT,
                    trade_id        TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_signals_symbol
                    ON signals(symbol);
                CREATE INDEX IF NOT EXISTS idx_signals_status
                    ON signals(status);

                CREATE TABLE IF NOT EXISTS metrics_counters (
                    name        TEXT PRIMARY KEY,
                    value       INTEGER NOT NULL DEFAULT 0,
                    updated_at  INTEGER
                );

                CREATE TABLE IF NOT EXISTS metrics_gauges (
                    name        TEXT PRIMARY KEY,
                    value       REAL NOT NULL DEFAULT 0,
                    updated_at  INTEGER
                );

                CREATE TABLE IF NOT EXISTS event_outbox (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    event       TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    sent        INTEGER NOT NULL DEFAULT 0,
                    created_at  INTEGER NOT NULL,
                    sent_at     INTEGER
                );

                CREATE INDEX IF NOT EXISTS idx_outbox_sent
                    ON event_outbox(sent);
            """
            )
        logger.info("Database initialised", extra={"path": self._path})

    # ── Trades ────────────────────────────────────────────────────────────

    def upsert_trade(self, trade) -> None:
        """Insert or update a trade record."""
        from src.utils.time import now_ms

        plan_json = None
        if trade.plan:
            try:
                plan_json = json.dumps(
                    trade.plan.to_dict()
                    if hasattr(trade.plan, "to_dict")
                    else {
                        "signalId": trade.plan.signal_id,
                        "lotSize": trade.plan.lot_size,
                        "riskAmount": trade.plan.risk_amount,
                        "riskPercent": trade.plan.risk_percent,
                        "riskRewardRatio": trade.plan.risk_reward_ratio,
                    }
                )
            except Exception:
                pass

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO trades (
                    id, signal_id, symbol, side, status,
                    entry_ticket, entry_price, entry_lots, current_lots,
                    stop_loss, tp1, tp2,
                    tp1_hit, tp1_hit_at, tp2_hit, tp2_hit_at,
                    sl_hit, sl_hit_at,
                    close_reason, close_price, realized_pnl, realized_rr,
                    plan_json, opened_at, closed_at, created_at, updated_at
                ) VALUES (
                    :id, :signal_id, :symbol, :side, :status,
                    :entry_ticket, :entry_price, :entry_lots, :current_lots,
                    :stop_loss, :tp1, :tp2,
                    :tp1_hit, :tp1_hit_at, :tp2_hit, :tp2_hit_at,
                    :sl_hit, :sl_hit_at,
                    :close_reason, :close_price, :realized_pnl, :realized_rr,
                    :plan_json, :opened_at, :closed_at, :created_at, :updated_at
                )
                ON CONFLICT(id) DO UPDATE SET
                    status       = excluded.status,
                    current_lots = excluded.current_lots,
                    stop_loss    = excluded.stop_loss,
                    tp1_hit      = excluded.tp1_hit,
                    tp1_hit_at   = excluded.tp1_hit_at,
                    tp2_hit      = excluded.tp2_hit,
                    tp2_hit_at   = excluded.tp2_hit_at,
                    sl_hit       = excluded.sl_hit,
                    sl_hit_at    = excluded.sl_hit_at,
                    close_reason = excluded.close_reason,
                    close_price  = excluded.close_price,
                    realized_pnl = excluded.realized_pnl,
                    realized_rr  = excluded.realized_rr,
                    closed_at    = excluded.closed_at,
                    updated_at   = excluded.updated_at
            """,
                {
                    "id": trade.id,
                    "signal_id": trade.signal_id,
                    "symbol": trade.symbol,
                    "side": trade.side.value,
                    "status": trade.status.value,
                    "entry_ticket": trade.entry_ticket,
                    "entry_price": trade.entry_price,
                    "entry_lots": trade.entry_lots,
                    "current_lots": trade.current_lots,
                    "stop_loss": trade.stop_loss,
                    "tp1": trade.tp1,
                    "tp2": trade.tp2,
                    "tp1_hit": int(trade.tp1_hit or False),
                    "tp1_hit_at": trade.tp1_hit_at,
                    "tp2_hit": int(trade.tp2_hit or False),
                    "tp2_hit_at": trade.tp2_hit_at,
                    "sl_hit": int(trade.sl_hit or False),
                    "sl_hit_at": trade.sl_hit_at,
                    "close_reason": (
                        trade.close_reason.value if trade.close_reason else None
                    ),
                    "close_price": trade.close_price,
                    "realized_pnl": trade.realized_pnl,
                    "realized_rr": trade.realized_rr,
                    "plan_json": plan_json,
                    "opened_at": trade.opened_at,
                    "closed_at": trade.closed_at,
                    "created_at": trade.created_at,
                    "updated_at": trade.updated_at or now_ms(),
                },
            )

    def load_open_trades_raw(self) -> list[dict]:
        """Return all open/partially-closed trade rows as dicts."""
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                """
                SELECT * FROM trades
                WHERE status IN ('OPEN', 'PARTIALLY_CLOSED')
                ORDER BY opened_at ASC
            """
            )
            return [dict(row) for row in cur.fetchall()]

    def load_all_trades_raw(self) -> list[dict]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute("SELECT * FROM trades ORDER BY opened_at DESC")
            return [dict(row) for row in cur.fetchall()]

    # ── Signals ───────────────────────────────────────────────────────────

    def upsert_signal(
        self,
        signal,
        received_at: int,
        status: str,
        outcome: Optional[str] = None,
        trade_id: Optional[str] = None,
    ) -> None:
        rejection_candle = getattr(signal, "rejection_candle", None)
        pattern = (
            getattr(rejection_candle, "pattern", None) if rejection_candle else None
        )
        wick_ratio = (
            getattr(rejection_candle, "wick_ratio", None) if rejection_candle else None
        )

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO signals (
                    id, symbol, direction, status,
                    entry_price, stop_loss, tp1, tp2,
                    risk_reward, risk_pips, pattern, wick_ratio,
                    raw_json, received_at, triggered_at, outcome, trade_id
                ) VALUES (
                    :id, :symbol, :direction, :status,
                    :entry_price, :stop_loss, :tp1, :tp2,
                    :risk_reward, :risk_pips, :pattern, :wick_ratio,
                    :raw_json, :received_at, :triggered_at, :outcome, :trade_id
                )
                ON CONFLICT(id) DO UPDATE SET
                    status       = excluded.status,
                    triggered_at = excluded.triggered_at,
                    outcome      = excluded.outcome,
                    trade_id     = excluded.trade_id
            """,
                {
                    "id": signal.id,
                    "symbol": signal.resolved_symbol,
                    "direction": signal.direction.value,
                    "status": status,
                    "entry_price": getattr(signal, "entry_price", None),
                    "stop_loss": getattr(signal, "stop_loss", None),
                    "tp1": getattr(signal, "tp1", None),
                    "tp2": getattr(signal, "tp2", None),
                    "risk_reward": getattr(signal, "risk_reward_ratio", None),
                    "risk_pips": getattr(signal, "risk_pips", None),
                    "pattern": pattern,
                    "wick_ratio": wick_ratio,
                    "raw_json": None,
                    "received_at": received_at,
                    "triggered_at": getattr(signal, "triggered_at", None),
                    "outcome": outcome,
                    "trade_id": trade_id,
                },
            )

    def load_signals_raw(self, limit: int = 500) -> list[dict]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                "SELECT * FROM signals ORDER BY received_at DESC LIMIT ?", (limit,)
            )
            return [dict(row) for row in cur.fetchall()]

    # ── Metrics ───────────────────────────────────────────────────────────

    def save_metrics(self, counters: dict[str, int], gauges: dict[str, float]) -> None:
        from src.utils.time import now_ms

        ts = now_ms()
        with self._connect() as conn:
            for name, value in counters.items():
                conn.execute(
                    """
                    INSERT INTO metrics_counters (name, value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(name) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """,
                    (name, value, ts),
                )
            for name, value in gauges.items():
                conn.execute(
                    """
                    INSERT INTO metrics_gauges (name, value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(name) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """,
                    (name, value, ts),
                )

    def load_metrics(self) -> tuple[dict[str, int], dict[str, float]]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            counters = {
                row["name"]: row["value"]
                for row in conn.execute(
                    "SELECT name, value FROM metrics_counters"
                ).fetchall()
            }
            gauges = {
                row["name"]: row["value"]
                for row in conn.execute(
                    "SELECT name, value FROM metrics_gauges"
                ).fetchall()
            }
        return counters, gauges

    # ── Event outbox (2.11 — reliable event delivery) ─────────────────────

    def outbox_enqueue(self, event: str, payload_json: str) -> int:
        """
        Persist an event to the outbox before transmission.
        Returns the auto-incremented row ID so the caller can later mark it sent.
        """
        from src.utils.time import now_ms

        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO event_outbox (event, payload_json, sent, created_at) VALUES (?, ?, 0, ?)",
                (event, payload_json, now_ms()),
            )
            return cur.lastrowid  # type: ignore[return-value]

    def outbox_mark_sent(self, row_id: int) -> None:
        """Mark an outbox row as successfully delivered."""
        from src.utils.time import now_ms

        with self._connect() as conn:
            conn.execute(
                "UPDATE event_outbox SET sent=1, sent_at=? WHERE id=?",
                (now_ms(), row_id),
            )

    def outbox_load_pending(self, max_age_ms: int = 3_600_000) -> list[tuple[int, str, str]]:
        """
        Return all unsent outbox rows newer than ``max_age_ms`` milliseconds,
        oldest first.  Returns list of (row_id, event, payload_json).
        """
        from src.utils.time import now_ms

        cutoff = now_ms() - max_age_ms
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, event, payload_json FROM event_outbox "
                "WHERE sent=0 AND created_at >= ? ORDER BY id ASC",
                (cutoff,),
            ).fetchall()
        return [(r["id"], r["event"], r["payload_json"]) for r in rows]

    def outbox_evict_sent(self, older_than_ms: int = 86_400_000) -> int:
        """Delete sent outbox rows older than ``older_than_ms`` ms. Returns count deleted."""
        from src.utils.time import now_ms

        cutoff = now_ms() - older_than_ms
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM event_outbox WHERE sent=1 AND sent_at < ?", (cutoff,)
            )
            return cur.rowcount

    # ── Helpers ───────────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn
