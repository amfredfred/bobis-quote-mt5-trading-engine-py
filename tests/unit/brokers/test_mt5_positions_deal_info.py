"""Mt5Positions.get_deal_info_for_ticket: narrow-then-widen deal history
search - checks a cheap recent window first, only falls back to the full
7-day search on a miss (e.g. engine restarted while a position was open)."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.brokers.mt5.positions import Mt5Positions

_DEAL_REASON_SL = 4
_DEAL_REASON_TP = 5
_DEAL_REASON_CLIENT = 0
_DEAL_ENTRY_OUT = 1
_DEAL_ENTRY_IN = 0


def _deal(position_id: int, entry: int, price: float, reason: int) -> SimpleNamespace:
    return SimpleNamespace(position_id=position_id, entry=entry, price=price, reason=reason)


def _positions() -> Mt5Positions:
    client = MagicMock()
    client.mt5.DEAL_ENTRY_OUT = _DEAL_ENTRY_OUT
    client.mt5.DEAL_REASON_SL = _DEAL_REASON_SL
    client.mt5.DEAL_REASON_TP = _DEAL_REASON_TP
    return Mt5Positions(client)


def test_finds_deal_in_recent_window_without_widening():
    pos = _positions()
    pos._history_deals_get = MagicMock(return_value=[_deal(555, _DEAL_ENTRY_OUT, 94.8, _DEAL_REASON_SL)])

    result = pos.get_deal_info_for_ticket(555)

    assert result == (94.8, "SL")
    assert pos._history_deals_get.call_count == 1  # never widened - found on first pass
    first_call_from, first_call_to = pos._history_deals_get.call_args[0]
    assert (first_call_to - first_call_from) == Mt5Positions._RECENT_DEAL_WINDOW


def test_widens_to_seven_days_when_recent_window_misses():
    pos = _positions()
    pos._history_deals_get = MagicMock(
        side_effect=[
            [],  # recent window: nothing found (e.g. engine just restarted)
            [_deal(555, _DEAL_ENTRY_OUT, 110.4, _DEAL_REASON_TP)],  # 7-day window: found
        ]
    )

    result = pos.get_deal_info_for_ticket(555)

    assert result == (110.4, "TP")
    assert pos._history_deals_get.call_count == 2
    second_call_from, second_call_to = pos._history_deals_get.call_args_list[1][0]
    assert (second_call_to - second_call_from) == timedelta(days=7)


def test_returns_none_when_not_found_in_either_window():
    pos = _positions()
    pos._history_deals_get = MagicMock(return_value=[])

    assert pos.get_deal_info_for_ticket(555) is None
    assert pos._history_deals_get.call_count == 2


def test_non_sl_tp_reason_returns_reason_none():
    pos = _positions()
    pos._history_deals_get = MagicMock(
        return_value=[_deal(555, _DEAL_ENTRY_OUT, 100.0, _DEAL_REASON_CLIENT)]
    )

    result = pos.get_deal_info_for_ticket(555)

    assert result == (100.0, None)


def test_ignores_entry_in_deals_and_other_tickets():
    pos = _positions()
    pos._history_deals_get = MagicMock(
        return_value=[
            _deal(555, _DEAL_ENTRY_IN, 90.0, _DEAL_REASON_SL),   # wrong entry type
            _deal(999, _DEAL_ENTRY_OUT, 90.0, _DEAL_REASON_SL),  # wrong ticket
            _deal(555, _DEAL_ENTRY_OUT, 94.8, _DEAL_REASON_SL),  # the real match
        ]
    )

    result = pos.get_deal_info_for_ticket(555)

    assert result == (94.8, "SL")
