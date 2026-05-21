import { useState } from 'react'
import { useEngineSocket } from './hooks/useEngineSocket'
import { Header }          from './components/Header'
import { Sidebar }         from './components/Sidebar'
import { ChartPlaceholder } from './components/ChartPlaceholder'
import { RiskPanel }       from './components/RiskPanel'
import { MetricsPanel }    from './components/MetricsPanel'
import { PositionsTable }  from './components/PositionsTable'
import { SignalFeed }      from './components/SignalFeed'
import { SignalsPage }     from './components/SignalsPage'
import { LogsPage }        from './components/LogsPage'

export type NavPage = 'overview' | 'trades' | 'risk' | 'signals' | 'logs' | 'config'

export default function App() {
  const [page, setPage]   = useState<NavPage>('overview')
  const [state, sendCmd]  = useEngineSocket()

  return (
    <div className="h-screen flex flex-col bg-base bg-terminal-grid overflow-hidden select-none">
      <Header engine={state.engine} connected={state.connected} sendCommand={sendCmd} />

      {/* Reconnecting overlay */}
      {!state.connected && (
        <div className="absolute inset-0 z-50 flex items-center justify-center bg-base/90 pointer-events-none">
          <div className="text-center border border-white/[0.08] bg-surface px-8 py-6 rounded-lg">
            <div className="w-2 h-2 rounded-full bg-warn pulse-ok mx-auto mb-3" />
            <div className="font-mono text-[11px] text-ink-primary tracking-widest mb-1">CONNECTING TO ENGINE</div>
            <div className="font-mono text-[9px] text-ink-muted">{import.meta.env.VITE_ENGINE_WS_URL ?? 'ws://localhost:8080'}</div>
            <div className="font-mono text-[8px] text-ink-dim mt-2">Retrying every 3 seconds…</div>
          </div>
        </div>
      )}

      <div className="flex flex-1 overflow-hidden">
        <Sidebar activePage={page} onNavigate={setPage} />

        <main className="flex flex-col flex-1 overflow-hidden">

          {/* ── OVERVIEW ─────────────────────────────────────────────────── */}
          {page === 'overview' && (
            <>
              <div className="flex flex-1 overflow-hidden">
                <div className="flex flex-col flex-1 overflow-hidden">
                  <ChartPlaceholder symbol="XAUUSD" />
                  <PositionsTable trades={state.trades} sendCommand={sendCmd} />
                </div>
                <div className="flex flex-col shrink-0 overflow-hidden border-l border-white/[0.07]" style={{ width: 272 }}>
                  <RiskPanel guards={state.riskGuards} />
                  <MetricsPanel metrics={state.metrics} />
                </div>
              </div>
              <SignalFeed signals={state.signals} />
            </>
          )}

          {/* ── TRADES ───────────────────────────────────────────────────── */}
          {page === 'trades' && (
            <div className="flex-1 overflow-hidden flex flex-col">
              <PositionsTable trades={state.trades} sendCommand={sendCmd} fullPage />
            </div>
          )}

          {/* ── RISK ─────────────────────────────────────────────────────── */}
          {page === 'risk' && (
            <div className="flex flex-1 overflow-hidden gap-px p-0">
              <div className="flex flex-col flex-1 overflow-y-auto">
                <RiskPanel guards={state.riskGuards} expanded />
              </div>
              <div className="flex flex-col w-72 shrink-0 border-l border-white/[0.07] overflow-y-auto">
                <MetricsPanel metrics={state.metrics} />
              </div>
            </div>
          )}

          {/* ── SIGNALS ──────────────────────────────────────────────────── */}
          {page === 'signals' && <SignalsPage signals={state.signals} />}

          {/* ── LOGS ─────────────────────────────────────────────────────── */}
          {page === 'logs' && <LogsPage logs={state.logs} />}

          {/* ── CONFIG ───────────────────────────────────────────────────── */}
          {page === 'config' && (
            <div className="flex-1 overflow-y-auto p-6">
              <div className="section-label mb-4">ENGINE INFO</div>
              <div className="grid grid-cols-2 gap-px max-w-xl">
                {Object.entries(state.engine).map(([k, v]) => (
                  <div key={k} className="flex items-center justify-between border border-white/[0.06] bg-surface px-3 py-2">
                    <span className="font-mono text-[9px] text-ink-secondary">{k.toUpperCase()}</span>
                    <span className="font-mono text-[9px] text-ink-primary">{String(v)}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

        </main>
      </div>
    </div>
  )
}
