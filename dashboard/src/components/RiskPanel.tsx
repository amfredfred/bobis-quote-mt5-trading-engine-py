import type { RiskGuard } from '../types/engine'

interface Props { guards: RiskGuard[]; expanded?: boolean }

export function RiskPanel({ guards }: Props) {
  const allClear = guards.every(g => g.status === 'ACTIVE' && g.current_value < g.threshold)

  return (
    <div className="flex flex-col shrink-0 border-b border-white/[0.07]">

      {/* Section header */}
      <div className="h-7 flex items-center justify-between px-3 border-b border-white/[0.07] shrink-0">
        <span className="section-label">RISK GUARDS</span>
        <div className="flex items-center gap-1.5">
          <div className={`w-1 h-1 rounded-full ${allClear ? 'bg-ok pulse-ok' : 'bg-err'}`} />
          <span className={`font-mono text-[8px] ${allClear ? 'text-ok' : 'text-err'}`}>
            {allClear ? 'ALL CLEAR' : 'BREACHED'}
          </span>
        </div>
      </div>

      {/* Guards */}
      <div className="divide-y divide-white/[0.05]">
        {guards.map(guard => {
          const pct     = Math.min((guard.current_value / guard.threshold) * 100, 100)
          const danger  = pct > 80
          const warning = pct > 55
          const barCls  = danger ? 'bg-err' : warning ? 'bg-warn' : 'bg-ok'
          const valCls  = danger ? 'text-err' : warning ? 'text-warn' : 'text-ok'

          return (
            <div key={guard.id} className="px-3 py-2.5">
              <div className="flex items-center justify-between mb-1.5">
                <div className="flex items-center gap-1.5">
                  <div className={`w-1 h-1 rounded-full ${danger ? 'bg-err' : 'bg-ok/50'}`} />
                  <span className="font-mono text-[9px] text-ink-secondary tracking-wide">{guard.name}</span>
                </div>
                <span className={`font-mono text-[9px] font-semibold tabular-nums ${valCls}`}>
                  {guard.current_value.toFixed(2)}{guard.unit}
                </span>
              </div>

              {/* Progress bar */}
              <div className="h-[2px] bg-white/[0.06] rounded-full mb-1.5 overflow-hidden">
                <div
                  className={`h-full rounded-full transition-all duration-700 ${barCls} opacity-50`}
                  style={{ width: `${pct}%` }}
                />
              </div>

              <div className="flex items-center justify-between">
                <span className="font-mono text-[7px] text-ink-dim leading-none">{guard.description}</span>
                <span className="font-mono text-[7px] text-ink-muted tabular-nums">
                  LIMIT {guard.threshold.toFixed(1)}{guard.unit}
                </span>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
