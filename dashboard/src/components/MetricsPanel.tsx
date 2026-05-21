import type { Metrics } from '../types/engine'

interface Props { metrics: Metrics }

function Row({
  label, value, sub, positive,
}: {
  label: string
  value: string
  sub?: string
  positive?: boolean | null
}) {
  const valCls =
    positive === true  ? 'text-ok' :
    positive === false ? 'text-err' :
    'text-ink-primary'

  return (
    <div className="flex items-center justify-between py-[5px] border-b border-white/[0.04] last:border-0">
      <span className="font-mono text-[8px] text-ink-secondary">{label}</span>
      <div className="flex items-baseline gap-1">
        {sub && <span className="font-mono text-[7px] text-ink-muted">{sub}</span>}
        <span className={`font-mono text-[9px] font-medium tabular-nums ${valCls}`}>{value}</span>
      </div>
    </div>
  )
}

function SectionHead({ label }: { label: string }) {
  return <div className="section-label mt-2 mb-0.5 first:mt-0">{label}</div>
}

export function MetricsPanel({ metrics }: Props) {
  const ddColor =
    metrics.drawdown_pct > 2   ? 'text-err' :
    metrics.drawdown_pct > 1   ? 'text-warn' :
    'text-ok'

  return (
    <div className="flex flex-col flex-1 overflow-hidden">
      <div className="h-7 flex items-center px-3 border-b border-white/[0.07] shrink-0">
        <span className="section-label">METRICS</span>
      </div>

      <div className="px-3 py-2 overflow-y-auto flex-1">

        <SectionHead label="EQUITY" />
        <Row label="CURRENT"  value={`$${metrics.equity.toLocaleString('en-US', { minimumFractionDigits: 2 })}`} />
        <Row label="PEAK"     value={`$${metrics.peak_equity.toLocaleString('en-US', { minimumFractionDigits: 2 })}`} />
        <div className="flex items-center justify-between py-[5px] border-b border-white/[0.04]">
          <span className="font-mono text-[8px] text-ink-secondary">DRAWDOWN</span>
          <span className={`font-mono text-[9px] font-medium tabular-nums ${ddColor}`}>
            {metrics.drawdown_pct.toFixed(2)}%
          </span>
        </div>

        <SectionHead label="P&L" />
        <Row
          label="DAILY"
          value={`${metrics.daily_pnl >= 0 ? '+' : ''}$${metrics.daily_pnl.toFixed(2)}`}
          positive={metrics.daily_pnl >= 0}
        />

        <SectionHead label="TRADES" />
        <Row
          label="OPEN / MAX"
          value={`${metrics.open_trades} / ${metrics.max_trades}`}
          positive={metrics.open_trades < metrics.max_trades ? null : false}
        />
        <Row label="TODAY"    value={String(metrics.total_trades_today)} />
        <Row label="W / L"    value={`${metrics.winning_trades} / ${metrics.losing_trades}`} />
        <Row
          label="WIN RATE"
          value={`${metrics.win_rate.toFixed(1)}%`}
          positive={metrics.win_rate >= 60 ? true : metrics.win_rate < 45 ? false : null}
        />

        <SectionHead label="PERFORMANCE" />
        <Row label="PENDING"    value={String(metrics.pending_signals)} />
        <Row
          label="SIG→TRADE"
          value={`${metrics.signal_to_trade_ms} ms`}
          positive={metrics.signal_to_trade_ms < 200 ? true : false}
        />
      </div>
    </div>
  )
}
