import { X } from 'lucide-react'
import type { Trade } from '../types/engine'
import type { SendCommand } from '../hooks/useEngineSocket'

interface Props {
  trades: Trade[]
  sendCommand: SendCommand
  fullPage?: boolean
}

function fmtDur(sec: number): string {
  const h = Math.floor(sec / 3600)
  const m = Math.floor((sec % 3600) / 60)
  const s = sec % 60
  if (h > 0) return `${h}h ${m}m`
  if (m > 0) return `${m}m ${s}s`
  return `${s}s`
}

function fmtPrice(price: number): string {
  if (price > 1000) return price.toFixed(2)
  if (price > 100)  return price.toFixed(1)
  return price.toFixed(5)
}

const COLS = ['ID', 'SYMBOL', 'SIDE', 'ENTRY', 'CURRENT', 'SL', 'TP1', 'TP2', 'LOTS', 'STATE', 'P&L', 'AGE', '']

export function PositionsTable({ trades, sendCommand, fullPage = false }: Props) {
  const handleClose = (trade: Trade) => {
    const confirmed = window.confirm(
      `Close ${trade.side} ${trade.symbol} @ market?\nTrade ID: ${trade.id}`
    )
    if (confirmed) {
      sendCommand('cmd.close_trade', { trade_id: trade.id })
    }
  }

  return (
    <div
      className={[
        'flex flex-col border-t border-white/[0.07] overflow-hidden shrink-0',
        fullPage ? 'flex-1' : '',
      ].join(' ')}
      style={fullPage ? undefined : { maxHeight: 200 }}
    >
      {/* Header */}
      <div className="h-7 flex items-center justify-between px-3 border-b border-white/[0.07] shrink-0 bg-surface/50">
        <span className="section-label">
          OPEN POSITIONS&nbsp;
          <span className="text-ink-dim">[{trades.length}]</span>
        </span>
        <span className="font-mono text-[7px] text-ink-dim">MAGIC #{import.meta.env.VITE_MAGIC ?? '—'}</span>
      </div>

      {trades.length === 0 ? (
        <div className="flex items-center justify-center flex-1 py-4">
          <span className="font-mono text-[8px] text-ink-dim tracking-widest">NO OPEN POSITIONS</span>
        </div>
      ) : (
        <div className="overflow-y-auto overflow-x-auto flex-1">
          <table className="w-full min-w-max">
            <thead className="sticky top-0 bg-surface z-10">
              <tr className="border-b border-white/[0.05]">
                {COLS.map(col => (
                  <th key={col} className="px-3 py-1.5 text-left whitespace-nowrap">
                    <span className="font-mono text-[7px] text-ink-dim tracking-wider">{col}</span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {trades.map(t => (
                <tr
                  key={t.id}
                  className="border-b border-white/[0.04] hover:bg-white/[0.025] transition-colors group"
                >
                  <td className="px-3 py-2 whitespace-nowrap">
                    <span className="font-mono text-[8px] text-ink-muted">{t.id}</span>
                  </td>
                  <td className="px-3 py-2 whitespace-nowrap">
                    <span className="font-mono text-[9px] font-semibold text-ink-primary">{t.symbol}</span>
                  </td>
                  <td className="px-3 py-2 whitespace-nowrap">
                    <span className={[
                      'font-mono text-[8px] font-bold px-1.5 py-0.5 rounded-sm border',
                      t.side === 'BUY' ? 'text-ok border-ok/20 bg-ok/5' : 'text-err border-err/20 bg-err/5',
                    ].join(' ')}>
                      {t.side}
                    </span>
                  </td>
                  <td className="px-3 py-2 whitespace-nowrap">
                    <span className="font-mono text-[9px] text-ink-secondary tabular-nums">{fmtPrice(t.entry_price)}</span>
                  </td>
                  <td className="px-3 py-2 whitespace-nowrap">
                    <span className="font-mono text-[9px] text-ink-primary tabular-nums">{fmtPrice(t.current_price)}</span>
                  </td>
                  <td className="px-3 py-2 whitespace-nowrap">
                    <span className="font-mono text-[9px] text-err/60 tabular-nums">{fmtPrice(t.sl)}</span>
                  </td>
                  <td className="px-3 py-2 whitespace-nowrap">
                    <span className="font-mono text-[9px] text-ok/50 tabular-nums">{fmtPrice(t.tp1)}</span>
                  </td>
                  <td className="px-3 py-2 whitespace-nowrap">
                    <span className="font-mono text-[9px] text-ok/70 tabular-nums">{fmtPrice(t.tp2)}</span>
                  </td>
                  <td className="px-3 py-2 whitespace-nowrap">
                    <span className="font-mono text-[9px] text-ink-secondary tabular-nums">{t.lots.toFixed(2)}</span>
                  </td>
                  <td className="px-3 py-2 whitespace-nowrap">
                    <span className={[
                      'font-mono text-[8px] px-1.5 py-0.5 rounded-sm border',
                      t.state === 'PARTIALLY_CLOSED'
                        ? 'text-warn border-warn/20 bg-warn/5'
                        : 'text-ink-secondary border-white/[0.09] bg-white/[0.03]',
                    ].join(' ')}>
                      {t.state === 'PARTIALLY_CLOSED' ? 'PART.' : 'OPEN'}
                    </span>
                  </td>
                  <td className="px-3 py-2 whitespace-nowrap">
                    <span className={`font-mono text-[9px] font-semibold tabular-nums ${t.pnl >= 0 ? 'text-ok' : 'text-err'}`}>
                      {t.pnl >= 0 ? '+' : ''}${t.pnl.toFixed(2)}
                    </span>
                  </td>
                  <td className="px-3 py-2 whitespace-nowrap">
                    <span className="font-mono text-[9px] text-ink-muted tabular-nums">{fmtDur(t.duration_sec)}</span>
                  </td>
                  {/* Close button */}
                  <td className="px-2 py-2 whitespace-nowrap">
                    <button
                      onClick={() => handleClose(t)}
                      title="Close trade at market"
                      className="w-5 h-5 flex items-center justify-center text-ink-dim hover:text-err hover:bg-err/10 rounded border border-transparent hover:border-err/20 transition-all opacity-0 group-hover:opacity-100"
                    >
                      <X size={10} />
                    </button>
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
