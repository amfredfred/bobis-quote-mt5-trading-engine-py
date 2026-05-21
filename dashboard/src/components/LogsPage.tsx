import { useRef, useEffect } from 'react'
import type { LogEntry } from '../types/engine'

interface Props { logs: LogEntry[] }

const LEVEL_COLOR: Record<string, string> = {
  DEBUG:   'text-ink-dim',
  INFO:    'text-ink-secondary',
  WARNING: 'text-warn',
  WARN:    'text-warn',
  ERROR:   'text-err',
}

export function LogsPage({ logs }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [logs.length])

  return (
    <div className="flex flex-col flex-1 overflow-hidden">
      <div className="h-7 flex items-center justify-between px-3 border-b border-white/[0.07] shrink-0">
        <span className="section-label">ENGINE LOGS <span className="text-ink-dim">[{logs.length}]</span></span>
        <span className="font-mono text-[8px] text-ink-dim">AUTO-SCROLL</span>
      </div>

      {logs.length === 0 ? (
        <div className="flex items-center justify-center flex-1">
          <span className="font-mono text-[8px] text-ink-dim tracking-widest">NO LOGS — ENGINE NOT CONNECTED</span>
        </div>
      ) : (
        <div className="flex-1 overflow-y-auto font-mono text-[9px] leading-relaxed p-3">
          {logs.map((entry, i) => (
            <div key={i} className="flex items-start gap-3 py-px hover:bg-white/[0.02] px-1 rounded">
              <span className="text-ink-dim tabular-nums shrink-0 w-16">
                {new Date(entry.ts * 1000).toLocaleTimeString('en-US', { hour12: false })}
              </span>
              <span className={`shrink-0 w-14 ${LEVEL_COLOR[entry.level] ?? 'text-ink-secondary'}`}>
                {entry.level}
              </span>
              <span className="text-ink-muted shrink-0 w-32 truncate">{entry.name}</span>
              <span className="text-ink-secondary break-all">{entry.msg}</span>
            </div>
          ))}
          <div ref={bottomRef} />
        </div>
      )}
    </div>
  )
}
