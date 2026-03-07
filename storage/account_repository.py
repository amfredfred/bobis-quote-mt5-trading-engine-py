"""Daily account stats for loss-limit tracking."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, asdict
from typing import Optional

from utils.time_utils import today_key

logger = logging.getLogger(__name__)


@dataclass
class DailyStats:
    date: str
    start_balance: float
    realized_pnl: float = 0.0
    trade_count: int = 0
    win_count: int = 0
    loss_count: int = 0


class AccountRepository:
    def __init__(self, storage_path: str) -> None:
        self._dir = os.path.join(storage_path, "account")

    def init(self) -> None:
        os.makedirs(self._dir, exist_ok=True)

    def get_today_stats(self, start_balance: float) -> DailyStats:
        date = today_key()
        stats = self._load(date)
        if stats is None:
            stats = DailyStats(date=date, start_balance=start_balance)
            self._save(stats)
        return stats

    def record_trade_closed(
        self,
        pnl: float,
        win: bool,
        start_balance: float,
    ) -> DailyStats:
        stats = self.get_today_stats(start_balance)
        stats.realized_pnl += pnl
        stats.trade_count += 1
        if win:
            stats.win_count += 1
        else:
            stats.loss_count += 1
        self._save(stats)
        return stats

    @staticmethod
    def daily_loss_percent(stats: DailyStats) -> float:
        """Daily loss as a percentage of starting balance (positive = loss)."""
        if stats.start_balance == 0:
            return 0.0
        loss = -stats.realized_pnl
        return max(0.0, (loss / stats.start_balance) * 100.0)

    # ── Private ───────────────────────────────────────────────────────────

    def _save(self, stats: DailyStats) -> None:
        path = os.path.join(self._dir, f"{stats.date}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(stats), f, indent=2)

    def _load(self, date: str) -> Optional[DailyStats]:
        path = os.path.join(self._dir, f"{date}.json")
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
                return DailyStats(**d)
        except Exception:
            logger.exception("AccountRepository: failed to load %s", date)
            return None
