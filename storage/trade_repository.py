"""
JSON-file trade persistence.

One file per trade: <storage_path>/trades/<id>.json
Swap for SQLite or Postgres without changing consumers.
"""

from __future__ import annotations

import json
import logging
import os
from typing import List, Optional

from interfaces.trade import Trade, TradeStatus

logger = logging.getLogger(__name__)


class TradeRepository:
    def __init__(self, storage_path: str) -> None:
        self._dir = os.path.join(storage_path, "trades")

    def init(self) -> None:
        os.makedirs(self._dir, exist_ok=True)
        logger.info("TradeRepository initialised", extra={"dir": self._dir})

    def save(self, trade: Trade) -> None:
        path = self._file_path(trade.id)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(trade.to_dict(), f, indent=2)

    def load(self, trade_id: str) -> Optional[Trade]:
        path = self._file_path(trade_id)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return self._from_dict(json.load(f))
        except Exception:
            logger.exception("TradeRepository: failed to load trade %s", trade_id)
            return None

    def load_all(self) -> List[Trade]:
        trades: List[Trade] = []
        if not os.path.isdir(self._dir):
            return trades
        for filename in os.listdir(self._dir):
            if not filename.endswith(".json"):
                continue
            path = os.path.join(self._dir, filename)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    trade = self._from_dict(json.load(f))
                    if trade:
                        trades.append(trade)
            except Exception:
                logger.warning("TradeRepository: skipping unreadable file %s", filename)
        return trades

    def load_open_trades(self) -> List[Trade]:
        return [
            t
            for t in self.load_all()
            if t.status in (TradeStatus.OPEN, TradeStatus.PARTIALLY_CLOSED)
        ]

    # ── Private ───────────────────────────────────────────────────────────

    def _file_path(self, trade_id: str) -> str:
        return os.path.join(self._dir, f"{trade_id}.json")

    @staticmethod
    def _from_dict(d: dict) -> Optional[Trade]:
        """
        Reconstruct a Trade from its persisted dict.

        The plan.signal field is not re-inflated (signal data lives in
        the signal engine).  plan.signal is set to None on reload.
        """
        from interfaces.trade import OrderSide, TradePlan, CloseReason, TradeStatus
        from interfaces.signal_interface import SignalDirection, InboundSignal
        import dataclasses

        try:
            plan_d = d.get("plan", {})

            # Minimal stub signal so TradePlan.signal is not None
            # Full signal data is available from the signal engine if needed.
            stub_signal = None

            plan = TradePlan(
                signal_id=plan_d.get("signalId", d["signalId"]),
                symbol=d["symbol"],
                side=OrderSide(d["side"]),
                entry_price=d.get("entryPrice") or 0.0,
                stop_loss=d.get("stopLoss") or 0.0,
                tp1=d.get("tp1") or 0.0,
                tp2=d.get("tp2") or 0.0,
                lot_size=plan_d.get("lotSize", 0.0),
                tp1_lot_size=plan_d.get("tp1LotSize", 0.0),
                tp2_lot_size=plan_d.get("tp2LotSize", 0.0),
                risk_amount=plan_d.get("riskAmount", 0.0),
                risk_percent=plan_d.get("riskPercent", 0.0),
                risk_reward_ratio=plan_d.get("riskRewardRatio", 0.0),
                planned_at=0,
                signal=stub_signal,
            )

            return Trade(
                id=d["id"],
                signal_id=d["signalId"],
                symbol=d["symbol"],
                side=OrderSide(d["side"]),
                status=TradeStatus(d["status"]),
                plan=plan,
                entry_ticket=d.get("entryTicket"),
                entry_price=d.get("entryPrice"),
                entry_lots=d.get("entryLots", 0.0),
                current_lots=d.get("currentLots", 0.0),
                stop_loss=d.get("stopLoss", 0.0),
                tp1=d.get("tp1", 0.0),
                tp2=d.get("tp2", 0.0),
                tp1_hit=d.get("tp1Hit", False),
                tp1_hit_at=d.get("tp1HitAt"),
                tp2_hit=d.get("tp2Hit", False),
                tp2_hit_at=d.get("tp2HitAt"),
                sl_hit=d.get("slHit", False),
                sl_hit_at=d.get("slHitAt"),
                opened_at=d.get("openedAt"),
                closed_at=d.get("closedAt"),
                close_reason=(
                    CloseReason(d["closeReason"]) if d.get("closeReason") else None
                ),
                close_price=d.get("closePrice"),
                realized_pnl=d.get("realizedPnl"),
                realized_rr=d.get("realizedRR"),
                created_at=d.get("createdAt", 0),
                updated_at=d.get("updatedAt", 0),
            )
        except Exception:
            logger.exception("TradeRepository: failed to reconstruct Trade from dict")
            return None
