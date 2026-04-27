"""
Converts a validated, risk-approved signal into a concrete TradePlan.

Live-account adjustment:
  [2] Spread surcharge — the real cost of entering a trade includes the spread
      paid on entry.  If SPREAD_RISK_MULTIPLIER > 0, the spread (in price units)
      is added to the SL distance before sizing, so the lot size already accounts
      for the wider effective risk on a live account.

      Example:  entry=1.08450, SL=1.08200, spread=0.00020 (2 pips), multiplier=1.0
                raw_sl_distance = 0.00250
                adjusted        = 0.00250 + 1.0 × 0.00020 = 0.00270
                Result: slightly smaller lot size — you risk the same $ amount
                        even after paying the spread.

      Set SPREAD_RISK_MULTIPLIER=0.0 to disable (demo / tight ECN accounts).

  [3] Pessimistic entry — lot size is calculated against the worst possible fill
      price within the configured slippage limit (max_entry_slippage_pct_of_stop).
      This ensures the position never risks more than the target amount even if
      the broker fills at the edge of the allowed slippage band.

      Example (SHORT):  entry=0.70193, SL=0.70300, stop_dist=0.00107, max_slip=20%
                        max_slip_price    = 0.00107 * 0.20 = 0.000214
                        pessimistic_entry = 0.70193 - 0.000214 = 0.701716
                        raw_sl_distance   = 0.70300 - 0.701716 = 0.001284  (vs 0.00107)
                        Result: smaller lot size — actual risk stays ≤ target
                                regardless of where within the slip band fill lands.
"""

from __future__ import annotations

import logging

from src.config.settings import RiskConfig, ExecutionConfig
from src.domain.position import AccountInfo, SymbolInfo
from src.domain.signal_interface import InboundSignal, SignalDirection
from src.domain.trade import OrderSide, TradePlan
from src.utils.lot_calculator import calculate_lot_size, RiskMode
from src.utils.price import pip_size
from src.utils.time import now_ms

logger = logging.getLogger(__name__)


class TradePlanner:
    def __init__(self, risk_config: RiskConfig, exec_config: ExecutionConfig) -> None:
        self._risk = risk_config
        self._exec = exec_config

    def plan(
        self,
        signal: InboundSignal,
        account_info: AccountInfo,
        symbol_info: SymbolInfo,
    ) -> TradePlan:
        side = (
            OrderSide.BUY
            if signal.direction == SignalDirection.LONG
            else OrderSide.SELL
        )
        pip = pip_size(symbol_info.point, symbol_info.digits)

        # ── [2] Spread-adjusted stop loss distance ─────────────────────────
        spread_price = (
            (symbol_info.ask - symbol_info.bid)
            if symbol_info.ask and symbol_info.bid
            else 0.0
        )
        spread_surcharge = spread_price * self._exec.spread_risk_multiplier

        # ── [3] Pessimistic entry — size to worst fill within slippage limit ─
        # For SHORT: a lower fill widens the SL distance (entry moves away from SL).
        # For LONG:  a higher fill widens the SL distance.
        # Using the worst-case entry guarantees actual risk ≤ target risk amount
        # regardless of where within the slippage band the broker fills.
        # max_slip is expressed as a fraction of the stop distance — dynamic per trade.
        stop_distance = abs(signal.entry_price - signal.stop_loss)
        max_slip_price = self._exec.max_entry_slippage_pct_of_stop * stop_distance
        pessimistic_entry = (
            signal.entry_price - max_slip_price
            if signal.direction == SignalDirection.SHORT
            else signal.entry_price + max_slip_price
        )

        raw_sl_distance = abs(pessimistic_entry - signal.stop_loss)
        adjusted_sl_distance = raw_sl_distance + spread_surcharge

        if signal.direction == SignalDirection.LONG:
            sizing_sl = signal.entry_price - adjusted_sl_distance
        else:
            sizing_sl = signal.entry_price + adjusted_sl_distance

        # ── Lot size calculation ───────────────────────────────────────────
        calc = calculate_lot_size(
            account_balance=account_info.balance,
            risk_mode=RiskMode(self._risk.risk_mode),
            risk_percent=self._risk.risk_percent_per_trade,
            risk_fixed=self._risk.risk_fixed_amount,
            entry_price=signal.entry_price,
            stop_loss=sizing_sl,
            symbol_info=symbol_info,
            max_lot=self._risk.max_lot_size,
            min_lot=self._risk.min_lot_size,
        )

        # ── Static TP1 level — stored for poll-based partial-close detection ─
        # When the position manager poll sees price cross TP1 it closes tp1_lots
        # and (if configured) moves the broker SL to entry so the remaining
        # position runs to TP2 risk-free.
        # TP1 = entry ± (tp1_rr_multiple × raw_stop_distance), always relative
        # to the actual stop distance, independent of signal.tp1.
        raw_stop_distance = abs(signal.entry_price - signal.stop_loss)
        tp1_offset = self._exec.tp1_rr_multiple * raw_stop_distance
        static_tp1 = (
            signal.entry_price + tp1_offset
            if signal.direction == SignalDirection.LONG
            else signal.entry_price - tp1_offset
        )

        # ── TP1 partial-close lot size ─────────────────────────────────────
        # Pre-calculate how many lots to close at TP1 so the poll handler
        # doesn't need to re-derive it.  Floored to volume_step so the broker
        # always accepts the volume.  0.0 means "no partial close".
        #
        # TP1 is only meaningful when the trade has room to reach it before
        # TP2.  If the signal RRR <= TP1_RR_MULTIPLE there is no space between
        # the TP1 level and the final target, so tp1_lots is forced to 0.
        import math
        trade_rr = (
            abs(signal.tp2 - signal.entry_price) / raw_stop_distance
            if raw_stop_distance > 0
            else 0.0
        )
        volume_step = symbol_info.volume_step if symbol_info.volume_step else 0.01
        # NOTE: the guard `trade_rr > tp1_rr_multiple` is load-bearing for the
        # price level too.  Because static_tp1 = entry ± (tp1_rr_multiple × stop)
        # and tp2 = entry ± (trade_rr × stop), this inequality guarantees
        # static_tp1 is always strictly between entry and tp2.  Never remove
        # the guard without also adding an explicit price-level clamp.
        if self._exec.tp1_percentage > 0 and trade_rr > self._exec.tp1_rr_multiple:
            raw_tp1_lots = calc.lot_size * (self._exec.tp1_percentage / 100.0)
            tp1_lots = math.floor(raw_tp1_lots / volume_step) * volume_step
            tp1_lots = round(tp1_lots, 2)
            # Ensure at least one step and never consumes the full position
            if tp1_lots < volume_step:
                tp1_lots = 0.0
            elif tp1_lots >= calc.lot_size:
                tp1_lots = round(
                    math.floor((calc.lot_size - volume_step) / volume_step) * volume_step, 2
                )
        else:
            tp1_lots = 0.0
        risk_pct = (
            (calc.risk_amount / account_info.balance) * 100.0
            if account_info.balance
            else 0.0
        )

        plan = TradePlan(
            signal_id=signal.id,
            symbol=signal.symbol,
            side=side,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            tp1=static_tp1,
            tp2=signal.tp2,
            lot_size=calc.lot_size,
            tp1_lots=tp1_lots,
            risk_amount=calc.risk_amount,
            risk_percent=risk_pct,
            risk_reward_ratio=signal.risk_reward_ratio,
            planned_at=now_ms(),
            signal=signal,
        )

        logger.info(
            "Lot sizing adjustments applied",
            extra={
                "signal_id": signal.id,
                "symbol": signal.symbol,
                "side": side.value,
                "risk_mode": calc.risk_mode.value,
                "lot_size": calc.lot_size,
                "risk_amount": round(calc.risk_amount, 2),
                "risk_pct": round(risk_pct, 2),
                "spread_pips": round(spread_price / pip, 1) if spread_price else 0,
                "surcharge_pips": round(spread_surcharge / pip, 1),
                "slippage_buffer_pct_of_stop": round(self._exec.max_entry_slippage_pct_of_stop * 100, 1),
                "raw_sl_pips": round(
                    abs(signal.entry_price - signal.stop_loss) / pip, 1
                ),
                "pessimistic_sl_pips": round(raw_sl_distance / pip, 1),
                "adjusted_sl_pips": round(adjusted_sl_distance / pip, 1),
                "tp1_rr_multiple": self._exec.tp1_rr_multiple,
                "trade_rr": round(trade_rr, 2),
                "tp1_eligible": trade_rr > self._exec.tp1_rr_multiple,
                "tp1_percentage": self._exec.tp1_percentage,
                "tp1_lots": tp1_lots,
                "signal_tp1": signal.tp1,
                "static_tp1": round(static_tp1, 5),
                "tp1_overridden": signal.tp1 != static_tp1,
            },
        )
        return plan









