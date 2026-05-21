import { Settings, Bell, PauseCircle, PlayCircle } from 'lucide-react'
import type { EngineInfo } from '../types/engine'
import type { SendCommand } from '../hooks/useEngineSocket'

function formatUptime(sec: number): string {
  const h = Math.floor(sec / 3600)
  const m = Math.floor((sec % 3600) / 60)
  const s = sec % 60
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

interface Props {
  engine: EngineInfo
  connected: boolean
  sendCommand: SendCommand
}

const STATUS_COLOR: Record<EngineInfo['status'], string> = {
  RUNNING:    'text-ok',
  STOPPED:    'text-err',
  PAUSED:     'text-warn',
  CONNECTING: 'text-warn',
}

export function Header({ engine, connected, sendCommand }: Props) {
  const isPaused = engine.status === 'PAUSED'

  return (
    <header className="h-10 flex items-center gap-4 px-3 border-b border-white/[0.07] bg-surface shrink-0">

      {/* Logo */}
      <div className="flex items-center gap-2.5 min-w-[144px]">
        <div className="w-5 h-5 border border-white/[0.22] flex items-center justify-center rounded-sm">
          <span className="font-mono text-[8px] font-bold text-ink-primary tracking-tighter leading-none">EX</span>
        </div>
        <span className="font-mono text-[10px] font-semibold text-ink-primary tracking-[0.2em] uppercase">
          Exec&nbsp;Engine
        </span>
        <span className="font-mono text-[8px] text-ink-muted border border-white/[0.08] px-1 py-px rounded-sm leading-none">
          v{engine.version}
        </span>
      </div>

      <div className="h-4 w-px bg-white/[0.08]" />

      {/* Engine status */}
      <div className="flex items-center gap-1.5">
        <div className={`w-1.5 h-1.5 rounded-full ${connected ? 'bg-ok pulse-ok' : 'bg-err'}`} />
        <span className={`font-mono text-[10px] font-semibold tracking-wider ${STATUS_COLOR[engine.status]}`}>
          {engine.status}
        </span>
      </div>

      {/* Mode badge */}
      <div className="flex items-center gap-1.5">
        <span className="section-label">MODE</span>
        <span className="font-mono text-[9px] text-ink-primary border border-white/[0.12] px-1.5 py-px rounded-sm leading-tight">
          {engine.mode}
        </span>
      </div>

      <div className="h-4 w-px bg-white/[0.08]" />

      {/* Connections */}
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-1.5">
          <div className={`w-1.5 h-1.5 rounded-full ${engine.connected_mt5 ? 'bg-ok' : 'bg-err'}`} />
          <span className="font-mono text-[9px] text-ink-secondary">MT5</span>
        </div>
        <div className="flex items-center gap-1.5">
          <div className={`w-1.5 h-1.5 rounded-full ${engine.connected_signal_engine ? 'bg-ok' : 'bg-err'}`} />
          <span className="font-mono text-[9px] text-ink-secondary">SIG-ENG</span>
        </div>
      </div>

      <div className="h-4 w-px bg-white/[0.08]" />

      {/* Uptime */}
      <div className="flex items-center gap-1.5">
        <span className="section-label">UPTIME</span>
        <span className="font-mono text-[10px] text-ink-secondary tabular-nums">
          {formatUptime(engine.uptime_sec)}
        </span>
      </div>

      <div className="flex-1" />

      {/* Engine controls */}
      <div className="flex items-center gap-1">
        {/* Pause / Resume */}
        <button
          onClick={() => sendCommand(isPaused ? 'cmd.resume' : 'cmd.pause')}
          title={isPaused ? 'Resume signal processing' : 'Pause signal processing'}
          className={[
            'flex items-center gap-1.5 px-2 h-6 rounded border text-[9px] font-mono transition-colors',
            isPaused
              ? 'border-ok/30 text-ok bg-ok/5 hover:bg-ok/10'
              : 'border-warn/30 text-warn bg-warn/5 hover:bg-warn/10',
          ].join(' ')}
        >
          {isPaused
            ? <><PlayCircle size={11} /> RESUME</>
            : <><PauseCircle size={11} /> PAUSE</>
          }
        </button>

        <div className="h-4 w-px bg-white/[0.08] mx-1" />

        <button className="w-7 h-7 flex items-center justify-center text-ink-muted hover:text-ink-secondary rounded transition-colors hover:bg-white/[0.04]">
          <Bell size={12} strokeWidth={1.5} />
        </button>
        <button className="w-7 h-7 flex items-center justify-center text-ink-muted hover:text-ink-secondary rounded transition-colors hover:bg-white/[0.04]">
          <Settings size={12} strokeWidth={1.5} />
        </button>
      </div>
    </header>
  )
}
