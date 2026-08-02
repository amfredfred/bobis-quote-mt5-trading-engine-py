import time
from types import SimpleNamespace

import pytest

from src.infra.ui_bridge import UIBridge, _serialize_trade


class _Queue:
    def depth(self) -> int:
        return 0


def _throttle_stats(**overrides) -> dict:
    stats = {
        "enabled": True,
        "engaged": False,
        "multiplier": 1.0,
        "drawdown_r": 0.0,
        "threshold_r": 8.0,
        "release_r": 6.0,
        "window_days": 30,
        "samples": 0,
    }
    stats.update(overrides)
    return stats


def _guards_config() -> SimpleNamespace:
    return SimpleNamespace(
        risk=SimpleNamespace(
            max_daily_loss_percent=2.5,
            max_profit_drawdown_percent=2.0,
            rolling_window_size=2,
            rolling_drawdown_pct=2.0,
            cluster_risk=SimpleNamespace(enabled=False, groups=[]),
            balance_tier_base_threshold=500.0,
            balance_tier_base_cap_pct=5.0,
            balance_tier_floor_pct=0.05,
        )
    )


def _guards_bridge(throttle_stats: dict, *, tier_cap_pct: float = 5.0, tier_cap_amount: float = 25.0) -> UIBridge:
    bridge = UIBridge.__new__(UIBridge)
    bridge._container = SimpleNamespace(
        cluster_tracker=SimpleNamespace(stats=lambda: {}),
        equity_throttle=SimpleNamespace(stats=lambda: throttle_stats),
        loss_tracker=SimpleNamespace(
            balance_tier_cap_pct=lambda *a: tier_cap_pct,
            balance_tier_cap_amount=lambda *a: tier_cap_amount,
            balance_tier_cap=lambda *a: (tier_cap_pct, tier_cap_amount),
        ),
    )
    return bridge


_LT = {
    "daily_loss_pct": 0.0,
    "equity_drawdown_pct": 0.0,
    "paused": False,
    "pause_reason": "",
}


def test_risk_guards_no_longer_include_pressure_items() -> None:
    """Equity throttle / balance tier cap moved to _build_pressure — they
    never pause the engine or reject a trade, unlike guard1-4."""
    bridge = _guards_bridge(_throttle_stats(engaged=True, multiplier=0.5))
    guards = bridge._build_risk_guards(dict(_LT), _guards_config())

    ids = {g["id"] for g in guards}
    assert ids == {"guard1", "guard2", "guard3", "guard4"}


def test_pressure_includes_engaged_equity_throttle() -> None:
    bridge = _guards_bridge(
        _throttle_stats(engaged=True, multiplier=0.5, drawdown_r=9.2, samples=120)
    )
    pressure = bridge._build_pressure(_guards_config())

    p1 = next(p for p in pressure if p["id"] == "pressure1")
    assert p1["name"] == "EQUITY THROTTLE"
    assert p1["pressure_pct"] == 100.0  # dd_r 9.2 past threshold_r 8.0 -> clamped to 100
    assert p1["detail"] == "Multiplier 0.5× · 9.2R below peak"
    assert "Sizing at 0.5×" in p1["description"]
    assert "9.2R" in p1["description"]


def test_pressure_equity_throttle_enabled_not_engaged_still_shows_current_dd() -> None:
    """Regression: not-engaged used to hide drawdown_r entirely (only the
    static threshold rule showed) — current DD must stay visible either way."""
    bridge = _guards_bridge(
        _throttle_stats(enabled=True, engaged=False, multiplier=1.0, drawdown_r=3.4)
    )
    pressure = bridge._build_pressure(_guards_config())

    p1 = next(p for p in pressure if p["id"] == "pressure1")
    assert p1["pressure_pct"] == pytest.approx(42.5)  # 3.4 / 8.0 * 100 - moves continuously
    assert "3.4R" in p1["description"]
    assert "3.4R" in p1["detail"]


def test_pressure_equity_throttle_disabled_is_zero_pressure() -> None:
    bridge = _guards_bridge(_throttle_stats(enabled=False))
    pressure = bridge._build_pressure(_guards_config())

    p1 = next(p for p in pressure if p["id"] == "pressure1")
    assert p1["pressure_pct"] == 0.0
    assert p1["detail"] == "Disabled"
    assert "Halves risk" in p1["description"]


def test_pressure_includes_balance_tier_cap() -> None:
    bridge = _guards_bridge(_throttle_stats(), tier_cap_pct=1.25, tier_cap_amount=25.0)
    pressure = bridge._build_pressure(_guards_config())

    p2 = next(p for p in pressure if p["id"] == "pressure2")
    assert p2["name"] == "BALANCE TIER CAP"
    assert p2["pressure_pct"] == 75.0  # (1 - 1.25/5.0) * 100
    assert "1.25%" in p2["detail"]
    assert "$25.00" in p2["detail"]
    assert "1.25%" in p2["description"]
    assert "$25.00" in p2["description"]


def test_pressure_balance_tier_cap_zero_pressure_at_base_tier() -> None:
    bridge = _guards_bridge(_throttle_stats(), tier_cap_pct=5.0, tier_cap_amount=25.0)
    pressure = bridge._build_pressure(_guards_config())

    p2 = next(p for p in pressure if p["id"] == "pressure2")
    assert p2["pressure_pct"] == 0.0  # at base tier -> no reduction yet


class _Repo:
    def __init__(self, trades: list | None = None) -> None:
        self._trades = trades or []

    def load_all(self) -> list:
        return self._trades


def _bridge(trades: list | None = None) -> UIBridge:
    bridge = UIBridge.__new__(UIBridge)
    bridge._container = SimpleNamespace(
        signal_queue=_Queue(),
        trade_repo=_Repo(trades),
        mt5_client=SimpleNamespace(is_connected=lambda: True, mt5=SimpleNamespace(terminal_info=lambda: None)),
        signal_consumer=SimpleNamespace(is_connected=True),
        # _build_metrics_from now also builds riskGuards/pressure (so they
        # refresh on the periodic push, not just the connect-time snapshot).
        cluster_tracker=SimpleNamespace(stats=lambda: {}),
        equity_throttle=SimpleNamespace(stats=lambda: _throttle_stats()),
        loss_tracker=SimpleNamespace(
            balance_tier_cap_pct=lambda *a: 5.0,
            balance_tier_cap_amount=lambda *a: 25.0,
            balance_tier_cap=lambda *a: (5.0, 25.0),
        ),
        # Same periodic-refresh rationale as trades above — pendingOrders
        # (resting limit orders) is built here too, not just at connect.
        pending_order_store=SimpleNamespace(get_all=lambda: []),
    )
    bridge._clients = set()
    return bridge


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        signal_engine=SimpleNamespace(symbols=["XAUUSD"]),
        risk=SimpleNamespace(
            max_losing_streak=3,
            max_daily_loss_percent=2.0,
            max_profit_drawdown_percent=2.0,
            rolling_window_size=2,
            rolling_drawdown_pct=2.0,
            cluster_risk=SimpleNamespace(enabled=False, groups=[]),
            balance_tier_base_threshold=500.0,
            balance_tier_base_cap_pct=5.0,
            balance_tier_floor_pct=0.05,
            entry_drift=SimpleNamespace(enabled=False, max_drift_pct_of_risk=25.0),
        ),
    )


def test_metrics_includes_risk_guards_and_pressure_for_periodic_refresh() -> None:
    """riskGuards/pressure must be in the periodic METRICS_UPDATE payload too,
    not just the connect-time STATE_SNAPSHOT — otherwise they freeze at
    whatever they were when the browser tab first loaded."""
    metrics = _bridge()._build_metrics_from(
        lt={"start_of_day_equity": 5_000.0, "daily_loss_pct": 0.0, "daily_budget": 100.0},
        counters={}, gauges={}, open_trades=[], config=_config(),
    )
    assert {g["id"] for g in metrics["riskGuards"]} == {"guard1", "guard2", "guard3", "guard4"}
    assert {p["id"] for p in metrics["pressure"]} == {"pressure1", "pressure2"}


def test_metrics_use_final_trade_outcomes_for_win_rate() -> None:
    metrics = _bridge()._build_metrics_from(
        lt={
            "start_of_day_equity": 5_000.0,
            "daily_loss_pct": 0.0,
            "daily_budget": 100.0,
            "equity_peak": 5_000.0,
            "equity_drawdown_pct": 0.0,
        },
        counters={
            "trades.opened": 3,
            "trades.closed": 3,
            "trades.tp1_hit": 2,
            "trades.tp2_hit": 0,
            "trades.sl_hit": 2,
            "trades.winning": 1,
            "trades.losing": 2,
        },
        gauges={},
        open_trades=[],
        config=_config(),
        account={
            "balance": 4_970.0,
            "equity": 4_970.0,
            "free_margin": 4_970.0,
            "margin": 0.0,
            "margin_level": 0.0,
            "currency": "USD",
        },
    )

    assert metrics["winning_trades"] == 1
    assert metrics["losing_trades"] == 2
    assert metrics["total_trades"] == 3
    assert metrics["win_rate"] == 33.3
    assert metrics["trades_tp1_hit"] == 2
    assert metrics["daily_pnl"] == -30.0


def test_persisted_outcomes_exclude_unknown_from_win_loss_breakeven() -> None:
    """Regression: an UNKNOWN-close trade used to fall through _trade_outcome
    into "breakeven" (no explicit UNKNOWN branch existed), inflating the
    breakeven bucket and disagreeing with the live in-process counters,
    which exclude UNKNOWN entirely. It must land in its own bucket instead,
    while still counting toward total_closed (matching trades.closed, which
    increments regardless of close_reason)."""
    from datetime import datetime, timezone

    today_ms = int(
        datetime.now(tz=timezone.utc)
        .replace(hour=12, minute=0, second=0, microsecond=0)
        .timestamp() * 1000
    )
    unknown_trade = SimpleNamespace(
        status="CLOSED",
        closed_at=today_ms,
        close_reason="UNKNOWN",
        close_price=None,
        realized_rr=None,
        entry_price=100.0,
        side="BUY",
    )
    win_trade = SimpleNamespace(
        status="CLOSED",
        closed_at=today_ms,
        close_reason="TP2_HIT",
        close_price=110.0,
        realized_rr=3.0,
        entry_price=100.0,
        side="BUY",
    )

    metrics = _bridge(trades=[unknown_trade, win_trade])._build_metrics_from(
        lt={"start_of_day_equity": 5_000.0, "daily_loss_pct": 0.0, "daily_budget": 100.0},
        counters={}, gauges={}, open_trades=[], config=_config(),
    )

    assert metrics["total_trades"] == 2
    assert metrics["winning_trades"] == 1
    assert metrics["losing_trades"] == 0
    assert metrics["trades_breakeven"] == 0
    assert metrics["trades_unknown_outcome"] == 1
    assert metrics["win_rate"] == 50.0  # 1 win / 2 total, not diluted by breakeven miscount


def test_metrics_exposes_entry_drift_as_a_gauge_not_a_guard() -> None:
    metrics = _bridge()._build_metrics_from(
        lt={
            "start_of_day_equity": 5_000.0,
            "daily_loss_pct": 0.0,
            "daily_budget": 100.0,
            "equity_peak": 5_000.0,
            "equity_drawdown_pct": 0.0,
        },
        counters={},
        gauges={"risk.last_entry_drift_pct_of_risk": 13.4},
        open_trades=[],
        config=_config(),
    )

    assert metrics["entry_drift_pct_of_risk"] == 13.4
    assert metrics["entry_drift_max_pct_of_risk"] == 25.0


def test_metrics_exposes_signal_engine_connection_state() -> None:
    bridge = _bridge()
    bridge._container.signal_consumer.is_connected = False

    metrics = bridge._build_metrics_from(
        lt={
            "start_of_day_equity": 5_000.0,
            "daily_loss_pct": 0.0,
            "daily_budget": 100.0,
            "equity_peak": 5_000.0,
            "equity_drawdown_pct": 0.0,
        },
        counters={},
        gauges={},
        open_trades=[],
        config=_config(),
    )

    assert metrics["signal_engine_connected"] is False


def test_metrics_hydrate_final_outcomes_from_persisted_trades() -> None:
    now_ms = int(time.time() * 1000)
    metrics = _bridge(
        [
            SimpleNamespace(
                status="CLOSED",
                close_reason="TP2_HIT",
                realized_rr=0.0,
                side="BUY",
                entry_price=100.0,
                close_price=110.0,
                closed_at=now_ms,
            ),
            SimpleNamespace(
                status="CLOSED",
                close_reason="MANUAL",
                realized_rr=-0.5,
                side="SELL",
                entry_price=100.0,
                close_price=102.0,
                closed_at=now_ms,
            ),
            SimpleNamespace(
                status="CLOSED",
                close_reason="MANUAL",
                realized_rr=0.8,
                side="BUY",
                entry_price=100.0,
                close_price=99.0,
                closed_at=now_ms,
            ),
        ]
    )._build_metrics_from(
        lt={
            "start_of_day_equity": 5_000.0,
            "daily_loss_pct": 0.0,
            "daily_budget": 100.0,
            "equity_peak": 5_000.0,
            "equity_drawdown_pct": 0.0,
        },
        counters={},
        gauges={},
        open_trades=[],
        config=_config(),
    )

    assert metrics["winning_trades"] == 1
    assert metrics["losing_trades"] == 2
    assert metrics["total_trades"] == 3
    assert metrics["win_rate"] == 33.3


def test_event_queue_drops_oldest_when_full() -> None:
    """BUG-14: the bridge event queue is bounded; overflow drops oldest, not newest."""
    import asyncio

    from src.infra.ui_bridge import _EVENT_QUEUE_MAX

    bridge = _bridge()
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        bridge._queue = asyncio.Queue(maxsize=3)

        for i in range(5):
            bridge._enqueue_event((f"event-{i}", None))

        assert bridge._queue.qsize() == 3
        items = [bridge._queue.get_nowait()[0] for _ in range(3)]
        assert items == ["event-2", "event-3", "event-4"]
    finally:
        asyncio.set_event_loop(None)
        loop.close()
    assert _EVENT_QUEUE_MAX == 500


def test_metrics_show_positive_daily_pnl_from_equity_delta() -> None:
    metrics = _bridge()._build_metrics_from(
        lt={
            "start_of_day_equity": 5_000.0,
            "daily_loss_pct": 0.0,
            "daily_budget": 100.0,
            "equity_peak": 5_040.0,
            "equity_drawdown_pct": 0.0,
        },
        counters={"trades.closed": 1, "trades.winning": 1},
        gauges={},
        open_trades=[],
        config=_config(),
        account={
            "balance": 5_040.0,
            "equity": 5_040.0,
            "free_margin": 5_040.0,
            "margin": 0.0,
            "margin_level": 0.0,
            "currency": "USD",
        },
    )

    assert metrics["daily_pnl"] == 40.0


def _trade_stub(*, entry_price: float = 100.0, entry_ticket: int = 555) -> SimpleNamespace:
    return SimpleNamespace(
        id="trade-1",
        symbol="XAUUSD",
        side=SimpleNamespace(value="BUY"),
        status=SimpleNamespace(value="OPEN"),
        entry_price=entry_price,
        stop_loss=95.0,
        tp1=105.0,
        tp2=110.0,
        entry_lots=0.01,
        tp1_lots=0.0,
        entry_ticket=entry_ticket,
    )


def test_serialize_trade_uses_live_price_and_pnl_when_position_matched():
    """Regression: pnl/current_price used to be hardcoded to 0.0/entry_price
    for every open trade, regardless of real price movement."""
    trade = _trade_stub(entry_price=100.0)
    live_position = SimpleNamespace(current_price=104.5, profit=12.34)

    serialized = _serialize_trade(trade, live_position)

    assert serialized["current_price"] == 104.5
    assert serialized["pnl"] == 12.34
    assert serialized["entry_price"] == 100.0  # unaffected - still the stored entry


def test_serialize_trade_falls_back_when_no_live_position_matched():
    """No live MT5 position found for this ticket (e.g. fetch failed, or a
    stub/legacy record) - falls back to the old safe defaults rather than
    crashing on a None live_position."""
    trade = _trade_stub(entry_price=100.0)

    serialized = _serialize_trade(trade, None)

    assert serialized["current_price"] == 100.0
    assert serialized["pnl"] == 0.0


def test_live_positions_by_ticket_returns_empty_on_fetch_failure():
    bridge = UIBridge.__new__(UIBridge)

    class _BoomPositions:
        def get_open_positions(self, magic):
            raise RuntimeError("MT5 read failed")

    bridge._container = SimpleNamespace(mt5_positions=_BoomPositions())
    config = SimpleNamespace(execution=SimpleNamespace(magic=8858))

    assert bridge._live_positions_by_ticket(config) == {}


def test_live_positions_by_ticket_keys_by_ticket():
    bridge = UIBridge.__new__(UIBridge)
    pos = SimpleNamespace(ticket=555, current_price=104.5, profit=12.34)

    class _Positions:
        def get_open_positions(self, magic):
            assert magic == 8858
            return [pos]

    bridge._container = SimpleNamespace(mt5_positions=_Positions())
    config = SimpleNamespace(execution=SimpleNamespace(magic=8858))

    result = bridge._live_positions_by_ticket(config)
    assert result == {555: pos}


def test_metrics_from_includes_trades_for_periodic_refresh():
    """Regression: trades (and therefore pnl/current_price) used to only
    ever be sent once, in the connect-time STATE_SNAPSHOT - nothing
    refreshed an already-open position's numbers afterward, so they looked
    frozen forever. trades must now also be in the periodic METRICS_UPDATE
    payload, same as riskGuards/pressure."""
    trade = _trade_stub(entry_price=100.0, entry_ticket=777)
    metrics = _bridge()._build_metrics_from(
        lt={"start_of_day_equity": 5_000.0, "daily_loss_pct": 0.0, "daily_budget": 100.0},
        counters={}, gauges={}, open_trades=[trade], config=_config(),
    )
    assert len(metrics["trades"]) == 1
    assert metrics["trades"][0]["ticket"] == 777
    assert metrics["trades"][0]["symbol"] == "XAUUSD"
