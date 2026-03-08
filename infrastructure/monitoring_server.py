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
from typing import TYPE_CHECKING

from infrastructure.metrics import metrics

if TYPE_CHECKING:
    from app.container import AppContainer
    from config.config import AppConfig

logger = logging.getLogger(__name__)

_started_at = time.time()


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
                    if self.path == "/" or self.path == "":
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

            def _json(self, code: int, data: dict):
                body = json.dumps(data, indent=2, default=str).encode()
                self._respond(code, body, "application/json")

            def _respond(self, code: int, body, content_type: str):
                if isinstance(body, str):
                    body = body.encode()
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, fmt, *args):
                pass  # suppress default access log — use engine logger instead

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


# ── HTML dashboard ────────────────────────────────────────────────────────────


def _render_dashboard(container: "AppContainer", config: "AppConfig") -> str:
    import datetime

    open_trades = container.position_store.get_open_trades()
    snap = metrics.snapshot()
    counters = snap.get("counters", {})
    gauges = snap.get("gauges", {})
    connected = container.mt5_client.is_connected()
    daily_loss = container.execution_engine._daily_loss_pct
    queue_depth = container.signal_queue.depth()
    uptime = _uptime()

    # ── Derived values ────────────────────────────────────────────────────
    hours, rem = divmod(int(uptime), 3600)
    mins, secs = divmod(rem, 60)
    uptime_fmt = f"{hours:02d}:{mins:02d}:{secs:02d}"
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn_color = "#22c55e" if connected else "#ef4444"
    conn_label = "CONNECTED" if connected else "DISCONNECTED"
    conn_pulse = "pulse-green" if connected else "pulse-red"

    loss_ratio = (
        daily_loss / config.risk.max_daily_loss_percent
        if config.risk.max_daily_loss_percent
        else 0
    )
    loss_bar_w = min(int(loss_ratio * 100), 100)
    loss_bar_color = (
        "#22c55e" if loss_ratio < 0.7 else "#f59e0b" if loss_ratio < 1.0 else "#ef4444"
    )
    loss_text_color = loss_bar_color

    trades_ratio = (
        len(open_trades) / config.risk.max_open_trades
        if config.risk.max_open_trades
        else 0
    )
    trades_bar_w = min(int(trades_ratio * 100), 100)
    trades_bar_color = (
        "#22c55e"
        if trades_ratio < 0.7
        else "#f59e0b" if trades_ratio < 1.0 else "#ef4444"
    )

    tp1_count = counters.get("trades.tp1_hit", 0)
    tp2_count = counters.get("trades.tp2_hit", 0)
    sl_count = counters.get("trades.sl_hit", 0)
    opened_count = counters.get("trades.opened", 0)
    rejected_count = counters.get("risk.rejected", 0)
    approved_count = counters.get("risk.approved", 0)
    signals_total = approved_count + rejected_count
    approval_rate = (
        f"{int(approved_count/signals_total*100)}%" if signals_total else "—"
    )
    win_total = tp1_count + tp2_count
    loss_total = sl_count
    winrate = (
        f"{int(win_total/(win_total+loss_total)*100)}%"
        if (win_total + loss_total)
        else "—"
    )

    # ── Positions table rows ──────────────────────────────────────────────
    trades_rows = ""
    for t in open_trades:
        is_stub = t.id.startswith("STUB_")
        is_buy = t.side.value == "BUY"
        side_color = "#22c55e" if is_buy else "#ef4444"
        side_bg = "rgba(34,197,94,.08)" if is_buy else "rgba(239,68,68,.08)"
        stub_pill = '<span class="pill pill-gray">STUB</span>' if is_stub else ""
        tp1_pill = '<span class="pill pill-green">TP1 ✓</span>' if t.tp1_hit else ""
        status_pill = f'<span class="pill pill-{"blue" if t.status.value == "OPEN" else "yellow"}">{t.status.value}</span>'

        # opened ago
        if t.opened_at:
            ago_sec = int(time.time() - t.opened_at / 1000)
            ago_h, ago_r = divmod(ago_sec, 3600)
            ago_m, ago_s = divmod(ago_r, 60)
            opened_ago = f"{ago_h}h {ago_m}m" if ago_h else f"{ago_m}m {ago_s}s"
        else:
            opened_ago = "—"

        trades_rows += f"""
        <tr>
          <td class="mono muted small">{t.entry_ticket}</td>
          <td><span class="symbol-badge">{t.symbol}</span>{stub_pill}</td>
          <td><span class="side-badge" style="color:{side_color};background:{side_bg}">{t.side.value}</span></td>
          <td class="mono">{t.entry_price}</td>
          <td class="mono danger">{t.stop_loss}</td>
          <td class="mono">{t.tp1} {tp1_pill}</td>
          <td class="mono success">{t.tp2}</td>
          <td class="mono">{t.current_lots}</td>
          <td>{status_pill}</td>
          <td class="mono muted small">{opened_ago}</td>
        </tr>"""

    if not trades_rows:
        trades_rows = (
            '<tr><td colspan="10" class="empty-row">— No open positions —</td></tr>'
        )

    # ── Metrics rows ──────────────────────────────────────────────────────
    counter_rows = ""
    for k, v in sorted(counters.items()):
        counter_rows += (
            f'<tr><td class="mono muted">{k}</td><td class="mono right">{v}</td></tr>'
        )

    gauge_rows = ""
    for k, v in sorted(gauges.items()):
        gauge_rows += (
            f'<tr><td class="mono muted">{k}</td><td class="mono right">{v}</td></tr>'
        )

    # ── Symbol exposure breakdown ─────────────────────────────────────────
    symbol_counts: dict = {}
    for t in open_trades:
        symbol_counts[t.symbol] = symbol_counts.get(t.symbol, 0) + 1

    symbol_rows = ""
    for sym, cnt in sorted(symbol_counts.items(), key=lambda x: -x[1]):
        bar_w = min(int(cnt / config.risk.max_exposure_per_symbol * 100), 100)
        bar_c = "#22c55e" if cnt < config.risk.max_exposure_per_symbol else "#ef4444"
        symbol_rows += f"""<tr>
          <td class="mono">{sym}</td>
          <td>
            <div class="mini-bar-wrap">
              <div class="mini-bar" style="width:{bar_w}%;background:{bar_c}"></div>
            </div>
          </td>
          <td class="mono right">{cnt} / {config.risk.max_exposure_per_symbol}</td>
        </tr>"""
    if not symbol_rows:
        symbol_rows = '<tr><td colspan="3" class="empty-row">No positions</td></tr>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Execution Engine</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&family=Inter:wght@300;400;500;600&display=swap');

*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

:root {{
  --bg:        #060608;
  --surface-1: #0d0d12;
  --surface-2: #111118;
  --border:    #1a1a28;
  --border-2:  #222235;
  --text:      #d4d4e8;
  --muted:     #4a4a66;
  --muted-2:   #333348;
  --accent:    #6366f1;
  --accent-2:  #818cf8;
  --green:     #22c55e;
  --red:       #ef4444;
  --yellow:    #f59e0b;
  --blue:      #3b82f6;
}}

body {{
  background: var(--bg);
  color: var(--text);
  font-family: 'Inter', sans-serif;
  font-size: 13px;
  line-height: 1.5;
  min-height: 100vh;
}}

.mono {{ font-family: 'JetBrains Mono', monospace; }}
.muted {{ color: var(--muted); }}
.small {{ font-size: 11px; }}
.right {{ text-align: right; }}
.success {{ color: var(--green); }}
.danger  {{ color: var(--red); }}

/* ── Header ─────────────────────────────────────────────────── */
header {{
  position: sticky;
  top: 0;
  z-index: 100;
  background: rgba(6,6,8,.92);
  backdrop-filter: blur(12px);
  border-bottom: 1px solid var(--border);
  padding: 0 28px;
  height: 52px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}}

.header-left {{
  display: flex;
  align-items: center;
  gap: 20px;
}}

.logo {{
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: .1em;
  color: var(--accent-2);
  display: flex;
  align-items: center;
  gap: 8px;
}}

.logo-icon {{
  width: 22px; height: 22px;
  background: linear-gradient(135deg, var(--accent), #a78bfa);
  border-radius: 5px;
  display: flex; align-items: center; justify-content: center;
  font-size: 11px;
}}

.header-status {{
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  font-family: 'JetBrains Mono', monospace;
  color: {conn_color};
  font-weight: 600;
  letter-spacing: .06em;
}}

.header-right {{
  display: flex;
  align-items: center;
  gap: 20px;
}}

.header-meta {{
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  color: var(--muted);
  display: flex;
  align-items: center;
  gap: 16px;
}}

.header-meta span {{ display: flex; align-items: center; gap: 5px; }}

.refresh-badge {{
  background: var(--surface-2);
  border: 1px solid var(--border-2);
  border-radius: 4px;
  padding: 3px 8px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  color: var(--muted);
  letter-spacing: .04em;
}}

/* ── Pulse animations ────────────────────────────────────────── */
@keyframes pulse-g {{ 0%,100% {{ box-shadow: 0 0 0 0 rgba(34,197,94,.5); }} 50% {{ box-shadow: 0 0 0 4px rgba(34,197,94,0); }} }}
@keyframes pulse-r {{ 0%,100% {{ box-shadow: 0 0 0 0 rgba(239,68,68,.5); }} 50% {{ box-shadow: 0 0 0 4px rgba(239,68,68,0); }} }}

.dot {{
  width: 7px; height: 7px;
  border-radius: 50%;
  background: {conn_color};
  animation: {'pulse-g' if connected else 'pulse-r'} 2s infinite;
}}

/* ── Layout ──────────────────────────────────────────────────── */
main {{
  padding: 24px 28px 48px;
  max-width: 1600px;
  margin: 0 auto;
}}

.section-label {{
  font-size: 10px;
  font-weight: 600;
  letter-spacing: .12em;
  text-transform: uppercase;
  color: var(--muted);
  margin-bottom: 12px;
  display: flex;
  align-items: center;
  gap: 10px;
}}

.section-label::after {{
  content: '';
  flex: 1;
  height: 1px;
  background: var(--border);
}}

/* ── Stat cards ──────────────────────────────────────────────── */
.stats-grid {{
  display: grid;
  grid-template-columns: repeat(8, 1fr);
  gap: 1px;
  background: var(--border);
  border: 1px solid var(--border);
  border-radius: 10px;
  overflow: hidden;
  margin-bottom: 24px;
}}

.stat {{
  background: var(--surface-1);
  padding: 16px 18px;
  position: relative;
}}

.stat::after {{
  content: '';
  position: absolute;
  bottom: 0; left: 18px; right: 18px;
  height: 2px;
  border-radius: 1px;
  background: transparent;
  transition: background .2s;
}}

.stat.accent::after {{ background: var(--accent); }}
.stat.green::after  {{ background: var(--green);  }}
.stat.red::after    {{ background: var(--red);    }}
.stat.yellow::after {{ background: var(--yellow); }}
.stat.blue::after   {{ background: var(--blue);   }}

.stat-label {{
  font-size: 10px;
  font-weight: 500;
  letter-spacing: .08em;
  text-transform: uppercase;
  color: var(--muted);
  margin-bottom: 10px;
  white-space: nowrap;
}}

.stat-value {{
  font-family: 'JetBrains Mono', monospace;
  font-size: 24px;
  font-weight: 600;
  line-height: 1;
  margin-bottom: 6px;
}}

.stat-sub {{
  font-size: 11px;
  color: var(--muted);
  font-family: 'JetBrains Mono', monospace;
}}

/* ── Progress bars ───────────────────────────────────────────── */
.bar-wrap {{
  width: 100%;
  height: 3px;
  background: var(--border-2);
  border-radius: 2px;
  margin-top: 8px;
  overflow: hidden;
}}

.bar-fill {{
  height: 100%;
  border-radius: 2px;
  transition: width .4s ease;
}}

/* ── Cards ───────────────────────────────────────────────────── */
.card {{
  background: var(--surface-1);
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
}}

.card-header {{
  padding: 14px 18px 10px;
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: space-between;
}}

.card-title {{
  font-size: 11px;
  font-weight: 600;
  letter-spacing: .1em;
  text-transform: uppercase;
  color: var(--muted);
}}

.card-body {{ padding: 0; }}

/* ── Grid layouts ────────────────────────────────────────────── */
.two-col {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 16px; }}
.three-col {{ display: grid; grid-template-columns: 2fr 1fr 1fr; gap: 16px; margin-bottom: 16px; }}
.four-col {{ display: grid; grid-template-columns: repeat(4,1fr); gap: 16px; margin-bottom: 16px; }}
.mb {{ margin-bottom: 16px; }}

/* ── Tables ──────────────────────────────────────────────────── */
table {{ width: 100%; border-collapse: collapse; }}

th {{
  font-size: 10px;
  letter-spacing: .08em;
  text-transform: uppercase;
  color: var(--muted);
  text-align: left;
  padding: 9px 16px;
  border-bottom: 1px solid var(--border);
  font-weight: 600;
  background: var(--surface-1);
  white-space: nowrap;
}}

td {{
  padding: 11px 16px;
  border-bottom: 1px solid var(--border);
  vertical-align: middle;
}}

tr:last-child td {{ border-bottom: none; }}
tbody tr:hover td {{ background: rgba(99,102,241,.04); }}

.empty-row {{
  text-align: center;
  color: var(--muted);
  padding: 32px 16px;
  font-size: 12px;
  letter-spacing: .05em;
}}

/* ── Badges & pills ──────────────────────────────────────────── */
.pill {{
  display: inline-flex;
  align-items: center;
  padding: 2px 7px;
  border-radius: 4px;
  font-size: 10px;
  font-weight: 600;
  letter-spacing: .04em;
  margin-left: 5px;
  vertical-align: middle;
}}

.pill-green  {{ background: rgba(34,197,94,.15);  color: #4ade80; }}
.pill-red    {{ background: rgba(239,68,68,.15);   color: #f87171; }}
.pill-yellow {{ background: rgba(245,158,11,.15);  color: #fbbf24; }}
.pill-gray   {{ background: rgba(100,100,120,.2);  color: #888; }}
.pill-blue   {{ background: rgba(59,130,246,.15);  color: #60a5fa; }}

.symbol-badge {{
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px;
  font-weight: 600;
  color: var(--text);
}}

.side-badge {{
  display: inline-block;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: .06em;
  font-family: 'JetBrains Mono', monospace;
}}

/* ── Mini bar ────────────────────────────────────────────────── */
.mini-bar-wrap {{
  width: 80px;
  height: 4px;
  background: var(--border-2);
  border-radius: 2px;
  overflow: hidden;
  display: inline-block;
  vertical-align: middle;
}}

.mini-bar {{
  height: 100%;
  border-radius: 2px;
}}

/* ── Risk gauge ──────────────────────────────────────────────── */
.gauge-row {{
  padding: 14px 18px;
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  gap: 12px;
}}

.gauge-row:last-child {{ border-bottom: none; }}

.gauge-label {{
  font-size: 12px;
  color: var(--muted);
  width: 130px;
  flex-shrink: 0;
}}

.gauge-bar-wrap {{
  flex: 1;
  height: 6px;
  background: var(--border-2);
  border-radius: 3px;
  overflow: hidden;
}}

.gauge-bar-fill {{
  height: 100%;
  border-radius: 3px;
  transition: width .5s ease;
}}

.gauge-val {{
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px;
  width: 80px;
  text-align: right;
  flex-shrink: 0;
}}

/* ── Config row ──────────────────────────────────────────────── */
.config-row {{
  padding: 10px 18px;
  border-bottom: 1px solid var(--border);
  display: flex;
  justify-content: space-between;
  align-items: center;
}}

.config-row:last-child {{ border-bottom: none; }}
.config-key   {{ color: var(--muted); font-size: 12px; }}
.config-val   {{ font-family: 'JetBrains Mono', monospace; font-size: 12px; color: var(--text); }}

</style>
</head>
<body>

<!-- ── Header ─────────────────────────────────────────────────── -->
<header>
  <div class="header-left">
    <div class="logo">
      <div class="logo-icon">⬡</div>
      EXEC ENGINE
    </div>
    <div class="header-status">
      <div class="dot"></div>
      MT5 {conn_label}
    </div>
  </div>
  <div class="header-right">
    <div class="header-meta">
      <span>⏱ {uptime_fmt}</span>
      <span>🕐 {now_str}</span>
    </div>
    <div class="refresh-badge">AUTO-REFRESH 5s</div>
  </div>
</header>

<main>

<!-- ── Primary stats bar ────────────────────────────────────── -->
<div class="stats-grid" style="margin-bottom:24px">

  <div class="stat accent">
    <div class="stat-label">Open Trades</div>
    <div class="stat-value" style="color:var(--accent-2)">{len(open_trades)}</div>
    <div class="stat-sub">of {config.risk.max_open_trades} max</div>
    <div class="bar-wrap"><div class="bar-fill" style="width:{trades_bar_w}%;background:{trades_bar_color}"></div></div>
  </div>

  <div class="stat {'red' if loss_ratio >= 1.0 else 'yellow' if loss_ratio >= 0.7 else 'green'}">
    <div class="stat-label">Daily Loss</div>
    <div class="stat-value" style="color:{loss_text_color}">{daily_loss:.2f}%</div>
    <div class="stat-sub">limit {config.risk.max_daily_loss_percent}%</div>
    <div class="bar-wrap"><div class="bar-fill" style="width:{loss_bar_w}%;background:{loss_bar_color}"></div></div>
  </div>

  <div class="stat {'yellow' if queue_depth > 5 else 'green'}">
    <div class="stat-label">Queue Depth</div>
    <div class="stat-value" style="color:{'var(--yellow)' if queue_depth > 5 else 'var(--green)'}">{queue_depth}</div>
    <div class="stat-sub">signals pending</div>
  </div>

  <div class="stat blue">
    <div class="stat-label">Signals Total</div>
    <div class="stat-value" style="color:var(--blue)">{signals_total}</div>
    <div class="stat-sub">approval {approval_rate}</div>
  </div>

  <div class="stat green">
    <div class="stat-label">Trades Opened</div>
    <div class="stat-value" style="color:var(--green)">{opened_count}</div>
    <div class="stat-sub">this session</div>
  </div>

  <div class="stat yellow">
    <div class="stat-label">Risk Rejected</div>
    <div class="stat-value" style="color:var(--yellow)">{rejected_count}</div>
    <div class="stat-sub">blocked signals</div>
  </div>

  <div class="stat green">
    <div class="stat-label">TP1 / TP2</div>
    <div class="stat-value" style="color:var(--green)">{tp1_count} / {tp2_count}</div>
    <div class="stat-sub">partial / full close</div>
  </div>

  <div class="stat {'red' if sl_count > tp2_count else 'green'}">
    <div class="stat-label">SL Hit / Win%</div>
    <div class="stat-value" style="color:{'var(--red)' if sl_count > tp2_count else 'var(--green)'}">{sl_count} / {winrate}</div>
    <div class="stat-sub">stops triggered</div>
  </div>

</div>

<!-- ── Open positions ────────────────────────────────────────── -->
<div class="section-label">Open Positions</div>
<div class="card mb">
  <div class="card-body">
    <table>
      <thead>
        <tr>
          <th>Ticket</th>
          <th>Symbol</th>
          <th>Side</th>
          <th>Entry</th>
          <th>Stop Loss</th>
          <th>TP1</th>
          <th>TP2</th>
          <th>Lots</th>
          <th>Status</th>
          <th>Opened</th>
        </tr>
      </thead>
      <tbody>{trades_rows}</tbody>
    </table>
  </div>
</div>

<!-- ── Bottom grid ───────────────────────────────────────────── -->
<div class="three-col">

  <!-- Risk gauges -->
  <div class="card">
    <div class="card-header">
      <div class="card-title">Risk Utilisation</div>
    </div>
    <div class="card-body">
      <div class="gauge-row">
        <div class="gauge-label">Open Trades</div>
        <div class="gauge-bar-wrap"><div class="gauge-bar-fill" style="width:{trades_bar_w}%;background:{trades_bar_color}"></div></div>
        <div class="gauge-val" style="color:{trades_bar_color}">{len(open_trades)} / {config.risk.max_open_trades}</div>
      </div>
      <div class="gauge-row">
        <div class="gauge-label">Daily Loss</div>
        <div class="gauge-bar-wrap"><div class="gauge-bar-fill" style="width:{loss_bar_w}%;background:{loss_bar_color}"></div></div>
        <div class="gauge-val" style="color:{loss_bar_color}">{daily_loss:.2f}% / {config.risk.max_daily_loss_percent}%</div>
      </div>
      <div class="gauge-row">
        <div class="gauge-label">Queue Pressure</div>
        <div class="gauge-bar-wrap"><div class="gauge-bar-fill" style="width:{min(queue_depth*2,100)}%;background:{'var(--yellow)' if queue_depth > 5 else 'var(--green)'}"></div></div>
        <div class="gauge-val">{queue_depth} / 50</div>
      </div>
    </div>
  </div>

  <!-- Symbol exposure -->
  <div class="card">
    <div class="card-header">
      <div class="card-title">Symbol Exposure</div>
    </div>
    <div class="card-body">
      <table>
        <tbody>{symbol_rows}</tbody>
      </table>
    </div>
  </div>

  <!-- Risk config -->
  <div class="card">
    <div class="card-header">
      <div class="card-title">Risk Config</div>
    </div>
    <div class="card-body">
      <div class="config-row"><span class="config-key">Mode</span><span class="config-val">{config.risk.risk_mode.value}</span></div>
      <div class="config-row"><span class="config-key">Risk / trade</span><span class="config-val">{config.risk.risk_percent_per_trade}%</span></div>
      <div class="config-row"><span class="config-key">Max trades</span><span class="config-val">{config.risk.max_open_trades}</span></div>
      <div class="config-row"><span class="config-key">Max / symbol</span><span class="config-val">{config.risk.max_exposure_per_symbol}</span></div>
      <div class="config-row"><span class="config-key">Daily limit</span><span class="config-val">{config.risk.max_daily_loss_percent}%</span></div>
      <div class="config-row"><span class="config-key">Min R:R</span><span class="config-val">{config.risk.min_rr_ratio}</span></div>
      <div class="config-row"><span class="config-key">Poll interval</span><span class="config-val">{config.position_poll_interval}s</span></div>
    </div>
  </div>

</div>

<!-- ── Counters + Gauges ──────────────────────────────────────── -->
<div class="two-col">
  <div class="card">
    <div class="card-header"><div class="card-title">Counters</div></div>
    <div class="card-body">
      <table>
        <tbody>{counter_rows if counter_rows else '<tr><td class="empty-row" colspan="2">No counters yet</td></tr>'}</tbody>
      </table>
    </div>
  </div>
  <div class="card">
    <div class="card-header"><div class="card-title">Gauges</div></div>
    <div class="card-body">
      <table>
        <tbody>{gauge_rows if gauge_rows else '<tr><td class="empty-row" colspan="2">No gauges yet</td></tr>'}</tbody>
      </table>
    </div>
  </div>
</div>

</main>

<script>
  // Live clock update without full page reload
  function tick() {{
    const el = document.querySelector('.header-meta span:last-child');
    if (el) {{
      const now = new Date();
      const pad = n => String(n).padStart(2,'0');
      el.textContent = '🕐 ' + now.getFullYear() + '-' +
        pad(now.getMonth()+1) + '-' + pad(now.getDate()) + ' ' +
        pad(now.getHours()) + ':' + pad(now.getMinutes()) + ':' + pad(now.getSeconds());
    }}
  }}
  setInterval(tick, 1000);

  // Full data refresh every 5s via fetch (no flicker)
  setTimeout(() => location.reload(), 5000);
</script>
</body>
</html>"""
