import type { SignalEvent } from '../types/engine'

interface Props { signals: SignalEvent[] }

const CHIP: Record<SignalEvent['status'], string> = {
  OPENED:    'text-ok border-ok/20 bg-ok/5',
  APPROVED:  'text-ok/70 border-ok/10',
  TRIGGERED: 'text-info/70 border-info/10',
  RECEIVED:  'text-ink-muted border-white/[0.08]',
  REJECTED:  'text-err border-err/20 bg-err/5',
}

export function SignalsPage({ signals }: Props) {
  return (
    <div className="flex flex-col flex-1 overflow-hidden">
      <div className="h-7 flex items-center justify-between px-3 border-b border-white/[0.07] shrink-0">
        <span className="section-label">SIGNAL FEED <span className="text-ink-dim">[{signals.length}]</span></span>
      </div>

      {signals.length === 0 ? (
        <div className="flex items-center justify-center flex-1">
          <span className="font-mono text-[8px] text-ink-dim tracking-widest">NO SIGNALS YET</span>
        </div>
      ) : (
        <div className="overflow-y-auto flex-1">
          <table className="w-full">
            <thead className="sticky top-0 bg-surface z-10">
              <tr className="border-b border-white/[0.05]">
                {['TIME', 'ID', 'SYMBOL', 'DIRECTION', 'STATUS', 'REASON'].map(col => (
                  <th key={col} className="px-4 py-2 text-left">
                    <span className="font-mono text-[7px] text-ink-dim tracking-wider">{col}</span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {signals.map((sig, i) => (
                <tr key={`${sig.id}-${i}`} className="border-b border-white/[0.04] hover:bg-white/[0.02] transition-colors">
                  <td className="px-4 py-2">
                    <span className="font-mono text-[9px] text-ink-muted tabular-nums">{sig.timestamp}</span>
                  </td>
                  <td className="px-4 py-2">
                    <span className="font-mono text-[8px] text-ink-dim">{sig.id}</span>
                  </td>
                  <td className="px-4 py-2">
                    <span className="font-mono text-[9px] font-semibold text-ink-primary">{sig.symbol}</span>
                  </td>
                  <td className="px-4 py-2">
                    <span className={`font-mono text-[9px] font-bold ${sig.direction === 'BUY' ? 'text-ok' : 'text-err'}`}>
                      {sig.direction}
                    </span>
                  </td>
                  <td className="px-4 py-2">
                    <span className={`font-mono text-[8px] px-1.5 py-px border rounded-sm ${CHIP[sig.status]}`}>
                      {sig.status}
                    </span>
                  </td>
                  <td className="px-4 py-2">
                    <span className="font-mono text-[8px] text-ink-dim">{sig.reason ?? '—'}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
