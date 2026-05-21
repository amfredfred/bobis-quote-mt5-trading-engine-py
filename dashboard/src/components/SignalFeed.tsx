import type { SignalEvent } from '../types/engine'

interface Props { signals: SignalEvent[] }

const CHIP: Record<SignalEvent['status'], string> = {
  OPENED:    'text-ok border-ok/20 bg-ok/5',
  APPROVED:  'text-ok/60 border-ok/10',
  TRIGGERED: 'text-info/60 border-info/10',
  RECEIVED:  'text-ink-muted border-white/[0.08]',
  REJECTED:  'text-err border-err/20 bg-err/5',
}

function Chip({ status }: { status: SignalEvent['status'] }) {
  return (
    <span className={`font-mono text-[7px] px-1.5 py-px border rounded-sm leading-none ${CHIP[status]}`}>
      {status}
    </span>
  )
}

export function SignalFeed({ signals }: Props) {
  const doubled = [...signals, ...signals]

  return (
    <div className="h-7 flex items-center border-t border-white/[0.07] bg-surface shrink-0 overflow-hidden">

      {/* Label */}
      <div className="flex items-center gap-2 px-3 h-full border-r border-white/[0.07] shrink-0">
        <div className="w-1 h-1 rounded-full bg-ok pulse-ok" />
        <span className="section-label">SIGNAL FEED</span>
      </div>

      {/* Ticker */}
      <div className="flex-1 overflow-hidden relative">
        {doubled.length === 0 ? (
          <span className="font-mono text-[8px] text-ink-dim px-4">NO SIGNALS</span>
        ) : (
          <div className="ticker-track flex items-center whitespace-nowrap">
            {doubled.map((sig, i) => (
              <div
                key={`${sig.id}-${i}`}
                className="inline-flex items-center gap-2 px-4 border-r border-white/[0.05] h-7"
              >
                <span className="font-mono text-[8px] text-ink-muted tabular-nums">{sig.timestamp}</span>
                <span className="font-mono text-[9px] font-semibold text-ink-primary">{sig.symbol}</span>
                <span className={`font-mono text-[8px] font-bold ${sig.direction === 'BUY' ? 'text-ok' : 'text-err'}`}>
                  {sig.direction}
                </span>
                <Chip status={sig.status} />
                {sig.reason && (
                  <span className="font-mono text-[7px] text-ink-dim">· {sig.reason}</span>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
