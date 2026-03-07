"""
MT5 account and position queries.
Each public method calls client.ensure_connected() first so the system
recovers automatically if the terminal was restarted.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from brokers.mt5.mt5_client import Mt5Client
from brokers.mt5.mt5_types import Mt5PositionType
from interfaces.position import AccountInfo, Position, PositionSide, SymbolInfo

logger = logging.getLogger(__name__)


class Mt5Positions:
    def __init__(self, client: Mt5Client) -> None:
        self._client = client

    @property
    def _mt5(self):
        return self._client.mt5

    # ── Account ───────────────────────────────────────────────────────────

    def get_account_info(self) -> AccountInfo:
        self._client.ensure_connected()
        info = self._mt5.account_info()
        if info is None:
            error = self._mt5.last_error()
            raise RuntimeError(f"account_info() failed: {error}")

        return AccountInfo(
            login=info.login,
            server=info.server,
            currency=info.currency,
            balance=info.balance,
            equity=info.equity,
            margin=info.margin,
            free_margin=info.margin_free,
            margin_level=info.margin_level,
            leverage=info.leverage,
        )

    # ── Symbol ────────────────────────────────────────────────────────────

    def get_symbol_info(self, symbol: str) -> SymbolInfo:
        self._client.ensure_connected()
        info = self._mt5.symbol_info(symbol)
        if info is None:
            error = self._mt5.last_error()
            raise RuntimeError(f"symbol_info({symbol!r}) failed: {error}")

        if not info.visible:
            self._mt5.symbol_select(symbol, True)
            info = self._mt5.symbol_info(symbol)

        tick = self._mt5.symbol_info_tick(symbol)
        ask = tick.ask if tick else 0.0
        bid = tick.bid if tick else 0.0

        return SymbolInfo(
            symbol=info.name,
            digits=info.digits,
            point=info.point,
            tick_size=info.trade_tick_size,
            tick_value=info.trade_tick_value,
            contract_size=info.trade_contract_size,
            lot_min=info.volume_min,
            lot_max=info.volume_max,
            lot_step=info.volume_step,
            spread=info.spread,
            ask=ask,
            bid=bid,
        )

    # ── Positions ─────────────────────────────────────────────────────────

    def get_open_positions(self, magic: Optional[int] = None) -> List[Position]:
        self._client.ensure_connected()
        raw = self._mt5.positions_get() or []
        if magic is not None:
            raw = [p for p in raw if p.magic == magic]

        return [
            Position(
                ticket=p.ticket,
                symbol=p.symbol,
                side=(
                    PositionSide.BUY
                    if p.type == Mt5PositionType.BUY
                    else PositionSide.SELL
                ),
                lots=p.volume,
                open_price=p.price_open,
                current_price=p.price_current,
                stop_loss=p.sl,
                take_profit=p.tp,
                swap=p.swap,
                commission=p.commission,
                profit=p.profit,
                open_time=int(p.time * 1000),
                comment=p.comment,
                magic=p.magic,
            )
            for p in raw
        ]

    def get_position_by_ticket(self, ticket: int) -> Optional[Position]:
        positions = self.get_open_positions()
        return next((p for p in positions if p.ticket == ticket), None)

    def get_current_tick(self, symbol: str):
        self._client.ensure_connected()
        return self._mt5.symbol_info_tick(symbol)
