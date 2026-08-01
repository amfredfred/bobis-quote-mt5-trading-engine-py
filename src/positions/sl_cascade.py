"""
Stacking SL cascade — when a new trade opens (market fill or limit fill)
on a symbol+direction that already has an open position, move the EARLIER
position's SL up to match the new trade's SL, provided that's actually more
favorable.

Explicit user directive, kept deliberately simple per their own framing —
"everything else sounds like overengineering": match on symbol + direction
ONLY, no zone/strategy/signal-id tracking. A later signal reconfirming the
same symbol+direction is read as the move continuing to develop, so its
(necessarily tighter) SL is used to lock in the earlier trade's risk —
"this means 0 risk for previous position and so on."

Called once a NEW trade is confirmed OPEN (market fill in
ExecutionEngine.execute(), or a limit fill in
PendingOrderManager._handle_fill) — not at signal arrival, since a
resting limit order that never fills should never cascade anything.
"""

from __future__ import annotations

import logging

from src.brokers.mt5.orders import Mt5Orders
from src.domain.trade import OrderSide, Trade
from src.positions.store import PositionStore

logger = logging.getLogger(__name__)


def _is_more_favorable(side: OrderSide, candidate_sl: float, current_sl: float) -> bool:
    """True when candidate_sl protects more of the position than current_sl —
    higher for BUY (closer to/past the market), lower for SELL."""
    if side == OrderSide.BUY:
        return candidate_sl > current_sl
    return candidate_sl < current_sl


def cascade_sl_to_stacked_position(
    store: PositionStore,
    mt5_orders: Mt5Orders,
    new_trade: Trade,
) -> None:
    """Find an already-open trade on the same symbol+direction as
    `new_trade` (excluding itself) and, if `new_trade`'s SL is more
    favorable, move that earlier trade's SL to match. Best-effort: logs
    and returns on any failure rather than blocking the new trade's own
    lifecycle — a failed cascade leaves the earlier trade at its prior
    (still valid) SL, not in an unprotected state.
    """
    for existing in store.get_open_trades():
        if existing.id == new_trade.id:
            continue
        if existing.symbol != new_trade.symbol or existing.side != new_trade.side:
            continue
        if existing.entry_ticket is None:
            continue
        if not _is_more_favorable(existing.side, new_trade.stop_loss, existing.stop_loss):
            continue

        try:
            mt5_orders.modify_position(
                ticket=existing.entry_ticket,
                sl=new_trade.stop_loss,
                tp=existing.tp2,
            )
        except Exception:
            logger.exception(
                "SL cascade failed — earlier stacked position stays at its prior SL",
                extra={
                    "existing_trade_id": existing.id,
                    "existing_ticket": existing.entry_ticket,
                    "new_trade_id": new_trade.id,
                    "symbol": new_trade.symbol,
                    "attempted_sl": new_trade.stop_loss,
                },
            )
            continue

        store.update(existing.id, stop_loss=new_trade.stop_loss)
        logger.info(
            "SL cascaded to earlier stacked position",
            extra={
                "existing_trade_id": existing.id,
                "existing_ticket": existing.entry_ticket,
                "new_trade_id": new_trade.id,
                "symbol": new_trade.symbol,
                "old_sl": existing.stop_loss,
                "new_sl": new_trade.stop_loss,
            },
        )
