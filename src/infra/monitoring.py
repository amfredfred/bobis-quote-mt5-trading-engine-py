"""
HTTP monitoring server.

Exposes read-only endpoints for observing engine state.
Runs in a daemon thread — does not block the main engine.

Endpoints:
    GET /              → HTML dashboard
    GET /health        → {"status": "ok"|"degraded", "uptime_sec": N}
    GET /status        → full engine state JSON
    GET /trades        → open trades from position store
    GET /metrics       → counters and gauges snapshot
    GET /queue         → signal queue depth

Usage:
    server = MonitoringServer(container, config, port=8080)
    server.start()
    server.stop()
"""

from __future__ import annotations

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import TYPE_CHECKING

from src.infra.metrics import metrics

if TYPE_CHECKING:
    from src.app.container import AppContainer
    from src.config.settings import AppConfig

logger = logging.getLogger(__name__)

_started_at = time.time()

# Dashboard HTML template — place dashboard.html in infrastructure/html/
_TEMPLATE_PATH = Path(__file__).parent / "html" / "dashboard.html"


# ── Server ────────────────────────────────────────────────────────────────────


class MonitoringServer:
    def __init__(
        self, container: "AppContainer", config: "AppConfig", port: int = 8080
    ) -> None:
        self._container = container
        self._config = config
        self._port = port
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        container = self._container
        config = self._config

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                try:
                    if self.path in ("/", ""):
                        body = _render_dashboard(container, config)
                        self._respond(200, body, "text/html; charset=utf-8")
                    elif self.path == "/health":
                        self._json(200, _health(container))
                    elif self.path == "/status":
                        self._json(200, _status(container, config))
                    elif self.path == "/trades":
                        self._json(200, _trades(container))
                    elif self.path == "/metrics":
                        self._json(200, metrics.snapshot())
                    elif self.path == "/queue":
                        self._json(200, {"depth": container.signal_queue.depth()})
                    else:
                        self._json(404, {"error": "not found"})
                except Exception as exc:
                    logger.exception("MonitoringServer handler error")
                    self._json(500, {"error": str(exc)})

            def _json(self, code: int, data: dict) -> None:
                body = json.dumps(data, indent=2, default=str).encode()
                self._respond(code, body, "application/json")

            def _respond(self, code: int, body, content_type: str) -> None:
                if isinstance(body, str):
                    body = body.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, fmt, *args) -> None:
                pass  # suppress default access log

        self._server = HTTPServer(("0.0.0.0", self._port), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="monitoring-http",
            daemon=True,
        )
        self._thread.start()
        logger.info("MonitoringServer started", extra={"port": self._port})

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
        logger.info("MonitoringServer stopped")


# ── Data builders ─────────────────────────────────────────────────────────────


def _uptime() -> float:
    return round(time.time() - _started_at, 1)


def _health(container: "AppContainer") -> dict:
    connected = container.mt5_client.is_connected()
    return {
        "status": "ok" if connected else "degraded",
        "mt5": "connected" if connected else "disconnected",
        "uptime_sec": _uptime(),
    }


def _status(container: "AppContainer", config: "AppConfig") -> dict:
    open_trades = container.position_store.get_open_trades()
    snap = metrics.snapshot()
    return {
        "uptime_sec": _uptime(),
        "mt5_connected": container.mt5_client.is_connected(),
        "queue_depth": container.signal_queue.depth(),
        "open_trades": len(open_trades),
        "daily_loss_pct": container.execution_engine._daily_loss_pct,
        "risk": {
            "max_open_trades": config.risk.max_open_trades,
            "max_exposure_per_symbol": config.risk.max_exposure_per_symbol,
            "max_daily_loss_percent": config.risk.max_daily_loss_percent,
            "min_rr_ratio": config.risk.min_rr_ratio,
        },
        "metrics": snap,
    }


def _trades(container: "AppContainer") -> dict:
    trades = container.position_store.get_open_trades()
    return {
        "count": len(trades),
        "trades": [
            {
                "id": t.id,
                "symbol": t.symbol,
                "side": t.side.value,
                "status": t.status.value,
                "ticket": t.entry_ticket,
                "entry_price": t.entry_price,
                "current_lots": t.current_lots,
                "sl": t.stop_loss,
                "tp1": t.tp1,
                "tp2": t.tp2,
                "tp1_hit": t.tp1_hit,
                "opened_at": t.opened_at,
                "stub": t.id.startswith("STUB_"),
            }
            for t in trades
        ],
    }


# ── Dashboard renderer ────────────────────────────────────────────────────────


def _render_dashboard(container: "AppContainer", config: "AppConfig") -> str:
    """
    Loads dashboard.html template and substitutes {placeholder} tokens.
    Uses plain str.replace() — CSS/JS curly braces in the template are
    never touched.
    """
    # ── Raw data ───────────────────────────────────────────────────────────
    open_trades = container.position_store.get_open_trades()
    snap = metrics.snapshot()
    counters = snap.get("counters", {})
    gauges = snap.get("gauges", {})
    connected = container.mt5_client.is_connected()
    daily_loss = container.execution_engine._daily_loss_pct
    queue_depth = container.signal_queue.depth()
    uptime = _uptime()

    # ── Uptime ─────────────────────────────────────────────────────────────
    hours, rem = divmod(int(uptime), 3600)
    mins, secs = divmod(rem, 60)
    uptime_fmt = f"{hours:02d}:{mins:02d}:{secs:02d}"

    # ── Connection ─────────────────────────────────────────────────────────
    conn_class = "connected" if connected else "disconnected"
    conn_label = "CONNECTED" if connected else "DISCONNECTED"

    # ── Counters ───────────────────────────────────────────────────────────
    tp1_count = counters.get("trades.tp1_hit", 0)
    tp2_count = counters.get("trades.tp2_hit", 0)
    sl_count = counters.get("trades.sl_hit", 0)
    opened_count = counters.get("trades.opened", 0)
    rejected_count = counters.get("risk.rejected", 0)
    approved_count = counters.get("risk.approved", 0)
    partial_fills = counters.get("orders.partial_fills", 0)
    orders_retried = counters.get("orders.retried", 0)
    emergency_closes = counters.get("orders.emergency_closes", 0)

    signals_total = approved_count + rejected_count
    approval_rate = (
        f"{int(approved_count / signals_total * 100)}%" if signals_total else "—"
    )

    win_total = tp1_count + tp2_count
    loss_total = sl_count
    winrate = (
        f"{int(win_total / (win_total + loss_total) * 100)}%"
        if (win_total + loss_total)
        else "—"
    )
    winrate_color = (
        "var(--green)"
        if win_total > loss_total
        else "var(--red)" if loss_total > win_total else "var(--yellow)"
    )

    # ── Config ─────────────────────────────────────────────────────────────
    open_count = len(open_trades)
    max_trades = config.risk.max_open_trades
    max_per_symbol = config.risk.max_exposure_per_symbol
    max_daily_loss = config.risk.max_daily_loss_percent
    min_rr = config.risk.min_rr_ratio
    risk_pct = config.risk.risk_percent_per_trade
    risk_mode = config.risk.risk_mode
    poll_interval = config.position_poll_interval

    # ── Bar calculations ───────────────────────────────────────────────────
    trades_ratio = open_count / max_trades if max_trades else 0
    trades_bar_w = min(int(trades_ratio * 100), 100)
    trades_bar_color = (
        "var(--green)"
        if trades_ratio < 0.7
        else "var(--yellow)" if trades_ratio < 1.0 else "var(--red)"
    )

    loss_ratio = daily_loss / max_daily_loss if max_daily_loss else 0
    loss_bar_w = min(int(loss_ratio * 100), 100)
    loss_color = (
        "var(--green)"
        if loss_ratio < 0.7
        else "var(--yellow)" if loss_ratio < 1.0 else "var(--red)"
    )

    queue_bar_w = min(int(queue_depth / 50 * 100), 100)
    queue_color = "var(--yellow)" if queue_depth > 5 else "var(--green)"

    # ── Latency ────────────────────────────────────────────────────────────
    def _fmt_ms(v):
        if v is None:
            return "—"
        if v >= 60_000:
            return f"{v/60000:.1f}m"
        if v >= 1_000:
            return f"{v/1000:.2f}s"
        return f"{int(v)}ms"

    def _lat_color(v, warn_ms, bad_ms):
        if v is None:
            return "var(--muted)"
        return (
            "var(--red)"
            if v > bad_ms
            else "var(--yellow)" if v > warn_ms else "var(--green)"
        )

    def _lat_bar(v, max_ms):
        if v is None:
            return 0
        return min(int(v / max_ms * 100), 100)

    lat_sig = gauges.get("latency.signal_to_trade_ms")
    lat_pip = gauges.get("latency.pipeline_ms")
    lat_brt = gauges.get("latency.broker_round_trip_ms")

    lat_sig_fmt = _fmt_ms(lat_sig)
    lat_pip_fmt = _fmt_ms(lat_pip)
    lat_brt_fmt = _fmt_ms(lat_brt)
    lat_sig_color = _lat_color(lat_sig, 10_000, 60_000)
    lat_pip_color = _lat_color(lat_pip, 1_000, 3_000)
    lat_brt_color = _lat_color(lat_brt, 500, 2_000)
    lat_sig_w = _lat_bar(lat_sig, 120_000)
    lat_pip_w = _lat_bar(lat_pip, 5_000)
    lat_brt_w = _lat_bar(lat_brt, 3_000)

    # ── Symbol exposure ────────────────────────────────────────────────────
    symbol_counts: dict = {}
    for t in open_trades:
        symbol_counts[t.symbol] = symbol_counts.get(t.symbol, 0) + 1

    symbol_exposure_rows = ""
    for sym, cnt in sorted(symbol_counts.items(), key=lambda x: -x[1]):
        bar_w = min(int(cnt / max_per_symbol * 100), 100)
        bar_c = "var(--red)" if cnt >= max_per_symbol else "var(--green)"
        symbol_exposure_rows += (
            f'<div class="exp-row">'
            f'<div class="exp-sym">{sym}</div>'
            f'<div class="exp-track">'
            f'<div class="exp-fill" style="width:{bar_w}%;background:{bar_c}"></div>'
            f"</div>"
            f'<div class="exp-count">{cnt}/{max_per_symbol}</div>'
            f"</div>"
        )
    if not symbol_exposure_rows:
        symbol_exposure_rows = (
            '<div style="font-size:11px;color:var(--muted);padding:4px 0">'
            "No open positions</div>"
        )

    # ── Positions table rows ───────────────────────────────────────────────
    trades_rows = ""
    for t in open_trades:
        side_cls = "badge-buy" if t.side.value == "BUY" else "badge-sell"
        stub_badge = (
            '<span class="badge badge-stub">STUB</span>'
            if t.id.startswith("STUB_")
            else ""
        )
        tp1_badge = (
            '<span class="badge badge-tp1">TP1&#10003;</span>' if t.tp1_hit else ""
        )

        if t.opened_at:
            ago_sec = int(time.time() - t.opened_at / 1000)
            ago_h, ago_r = divmod(ago_sec, 3600)
            ago_m, ago_s = divmod(ago_r, 60)
            opened_ago = f"{ago_h}h {ago_m}m" if ago_h else f"{ago_m}m {ago_s}s"
        else:
            opened_ago = "—"

        trades_rows += (
            f"<tr>"
            f"<td><span class='sym-label'>{t.symbol}</span>{stub_badge}</td>"
            f"<td><span class='badge {side_cls}'>{t.side.value}</span></td>"
            f"<td class='mono'>{t.entry_price}</td>"
            f"<td class='mono' style='color:var(--red)'>{t.stop_loss}</td>"
            f"<td class='mono'>{t.tp1}{tp1_badge}</td>"
            f"<td class='mono' style='color:var(--green)'>{t.tp2}</td>"
            f"<td class='mono'>{t.current_lots}</td>"
            f"<td><span class='badge badge-open'>{t.status.value}</span></td>"
            f"<td class='mono' style='color:var(--muted);font-size:10px'>{t.entry_ticket}</td>"
            f"<td style='color:var(--muted);font-size:11px'>{opened_ago}</td>"
            f"</tr>"
        )
    if not trades_rows:
        trades_rows = (
            '<tr><td colspan="10" class="empty-row">No open positions</td></tr>'
        )

    # ── Counter / gauge rows ───────────────────────────────────────────────
    counter_rows = "".join(
        f'<div class="metric-row">'
        f'<div class="m-key">{k}</div>'
        f'<div class="m-val">{v}</div>'
        f"</div>"
        for k, v in sorted(counters.items())
    ) or (
        '<div class="metric-row">'
        '<div class="m-key" style="width:100%;text-align:center;color:var(--muted)">'
        "No counters yet</div></div>"
    )

    gauge_rows = "".join(
        f'<div class="metric-row">'
        f'<div class="m-key">{k}</div>'
        f'<div class="m-val">{v}</div>'
        f"</div>"
        for k, v in sorted(gauges.items())
    ) or (
        '<div class="metric-row">'
        '<div class="m-key" style="width:100%;text-align:center;color:var(--muted)">'
        "No gauges yet</div></div>"
    )

    # ── Substitution map ───────────────────────────────────────────────────
    subs = {
        "conn_class": conn_class,
        "conn_label": conn_label,
        "uptime_fmt": uptime_fmt,
        "open_count": str(open_count),
        "max_trades": str(max_trades),
        "max_per_symbol": str(max_per_symbol),
        "max_daily_loss": str(max_daily_loss),
        "min_rr": str(min_rr),
        "risk_pct": str(risk_pct),
        "risk_mode": str(risk_mode),
        "poll_interval": str(poll_interval),
        "trades_bar_w": str(trades_bar_w),
        "trades_bar_color": trades_bar_color,
        "loss_bar_w": str(loss_bar_w),
        "loss_color": loss_color,
        "queue_depth": str(queue_depth),
        "queue_bar_w": str(queue_bar_w),
        "queue_color": queue_color,
        "winrate": winrate,
        "winrate_color": winrate_color,
        "win_total": str(win_total),
        "loss_total": str(loss_total),
        "tp1_count": str(tp1_count),
        "tp2_count": str(tp2_count),
        "sl_count": str(sl_count),
        "opened_count": str(opened_count),
        "approved_count": str(approved_count),
        "rejected_count": str(rejected_count),
        "signals_total": str(signals_total),
        "approval_rate": approval_rate,
        "partial_fills": str(partial_fills),
        "orders_retried": str(orders_retried),
        "emergency_closes": str(emergency_closes),
        "lat_sig_fmt": lat_sig_fmt,
        "lat_pip_fmt": lat_pip_fmt,
        "lat_brt_fmt": lat_brt_fmt,
        "lat_sig_color": lat_sig_color,
        "lat_pip_color": lat_pip_color,
        "lat_brt_color": lat_brt_color,
        "lat_sig_w": str(lat_sig_w),
        "lat_pip_w": str(lat_pip_w),
        "lat_brt_w": str(lat_brt_w),
        "symbol_exposure_rows": symbol_exposure_rows,
        "trades_rows": trades_rows,
        "counter_rows": counter_rows,
        "gauge_rows": gauge_rows,
    }

    # ── Load template and substitute ───────────────────────────────────────
    with open(_TEMPLATE_PATH, encoding="utf-8") as f:
        html = f.read()

    # Handle {daily_loss:.2f} format spec first — before the generic loop
    html = html.replace("{daily_loss:.2f}", f"{daily_loss:.2f}")

    # Safe substitution — only touches exact {key} tokens, ignores CSS/JS braces
    for key, value in subs.items():
        html = html.replace("{" + key + "}", value)

    return html









