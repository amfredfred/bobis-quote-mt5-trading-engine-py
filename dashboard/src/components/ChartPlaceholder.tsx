const TIMEFRAMES = ['1M', '5M', '15M', '1H', '4H', 'D1']
const Y_LABELS   = ['3 318.00', '3 312.00', '3 306.00', '3 300.00', '3 294.00', '3 288.00', '3 282.00']
const X_LABELS   = ['10:00', '11:00', '12:00', '13:00', '14:00', '15:00', '16:00']

interface Props { symbol: string }

export function ChartPlaceholder({ symbol }: Props) {
  return (
    <div className="flex flex-col flex-1 overflow-hidden border-b border-white/[0.07]">

      {/* Toolbar */}
      <div className="h-8 flex items-center gap-3 px-3 border-b border-white/[0.07] shrink-0 bg-surface/60">
        <div className="flex items-center gap-1.5">
          <span className="font-mono text-[11px] font-semibold text-ink-primary">{symbol}</span>
          <span className="font-mono text-[9px] text-ink-muted">·</span>
          <span className="font-mono text-[9px] text-ink-muted">SPOT</span>
        </div>

        <div className="h-3 w-px bg-white/[0.08]" />

        {/* Timeframe selector */}
        <div className="flex items-center gap-px">
          {TIMEFRAMES.map(tf => (
            <button
              key={tf}
              className={[
                'font-mono text-[9px] px-1.5 py-0.5 rounded transition-colors',
                tf === '4H'
                  ? 'text-ink-primary bg-white/[0.07] border border-white/[0.12]'
                  : 'text-ink-muted hover:text-ink-secondary hover:bg-white/[0.03]',
              ].join(' ')}
            >
              {tf}
            </button>
          ))}
        </div>

        <div className="flex-1" />

        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1.5">
            <span className="section-label">LAST</span>
            <span className="font-mono text-[11px] font-semibold text-ink-primary tabular-nums">3 302.80</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="section-label">SPREAD</span>
            <span className="font-mono text-[10px] text-ink-secondary tabular-nums">0.38</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="section-label">BID</span>
            <span className="font-mono text-[10px] text-err/70 tabular-nums">3 302.42</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="section-label">ASK</span>
            <span className="font-mono text-[10px] text-ok/70 tabular-nums">3 302.80</span>
          </div>
        </div>
      </div>

      {/* Chart body */}
      <div className="flex flex-1 overflow-hidden">

        {/* Canvas area */}
        <div className="relative flex-1 overflow-hidden">

          {/* Inner chart grid */}
          <div
            className="absolute inset-0 pointer-events-none"
            style={{
              backgroundImage: `
                linear-gradient(rgba(255,255,255,0.022) 1px, transparent 1px),
                linear-gradient(90deg, rgba(255,255,255,0.022) 1px, transparent 1px)
              `,
              backgroundSize: 'calc(100% / 7) calc(100% / 8)',
            }}
          />

          {/* CRT corner brackets */}
          {(['top-2 left-2 border-t border-l',
             'top-2 right-14 border-t border-r',
             'bottom-5 left-2 border-b border-l',
             'bottom-5 right-14 border-b border-r'] as const).map((cls, i) => (
            <div key={i} className={`absolute ${cls} w-3 h-3 border-white/[0.18] pointer-events-none`} />
          ))}

          {/* Static crosshair */}
          <div className="absolute inset-0 pointer-events-none">
            <div className="absolute top-1/2 left-0 right-14 h-px bg-white/[0.05]" />
            <div className="absolute left-[42%] top-0 bottom-5 w-px bg-white/[0.05]" />
          </div>

          {/* Trade level lines */}
          <div className="absolute left-0 right-14 pointer-events-none" style={{ top: '16%' }}>
            <div className="h-px w-full bg-ok/[0.25]" />
            <span className="absolute right-1 -top-3 font-mono text-[7px] text-ok/50">TP2 · 3 316.20</span>
          </div>
          <div className="absolute left-0 right-14 pointer-events-none" style={{ top: '31%' }}>
            <div className="h-px w-full bg-ok/[0.18]" style={{ borderTop: '1px dashed' }} />
            <span className="absolute right-1 -top-3 font-mono text-[7px] text-ok/40">TP1 · 3 305.80</span>
          </div>
          <div className="absolute left-0 right-14 pointer-events-none" style={{ top: '50%' }}>
            <div className="h-px w-full bg-white/[0.14]" />
            <span className="absolute right-1 -top-3 font-mono text-[7px] text-ink-muted/60">ENT · 3 295.40</span>
          </div>
          <div className="absolute left-0 right-14 pointer-events-none" style={{ top: '71%' }}>
            <div className="h-px w-full bg-err/[0.22]" />
            <span className="absolute right-1 -top-3 font-mono text-[7px] text-err/50">SL · 3 285.00</span>
          </div>

          {/* Watermark */}
          <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
            <div className="text-center">
              <div className="font-mono text-[10px] tracking-[0.35em] text-white/[0.035] uppercase font-semibold">
                CHART — NO DATA
              </div>
              <div className="font-mono text-[8px] tracking-[0.22em] text-white/[0.025] mt-1.5">
                AWAITING PRICE FEED
              </div>
            </div>
          </div>

          {/* X-axis labels */}
          <div className="absolute bottom-0 left-0 right-14 flex justify-between px-2 pb-1 pointer-events-none border-t border-white/[0.05]">
            {X_LABELS.map(lbl => (
              <span key={lbl} className="font-mono text-[7px] text-ink-dim">{lbl}</span>
            ))}
          </div>
        </div>

        {/* Y-axis */}
        <div className="w-14 shrink-0 flex flex-col justify-between items-end pr-2 pt-1 pb-5 border-l border-white/[0.05]">
          {Y_LABELS.map(lbl => (
            <span key={lbl} className="font-mono text-[7px] text-ink-dim tabular-nums">{lbl}</span>
          ))}
        </div>
      </div>
    </div>
  )
}
