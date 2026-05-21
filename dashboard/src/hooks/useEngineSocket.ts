import { useState, useEffect, useRef, useCallback } from 'react'
import type { EngineState } from '../types/engine'

const WS_URL = import.meta.env.VITE_ENGINE_WS_URL ?? 'ws://localhost:8080'

export type SendCommand = (type: string, payload?: object) => void

const EMPTY_STATE: EngineState = {
  connected: false,
  engine: {
    status: 'CONNECTING',
    uptime_sec: 0,
    mode: '—',
    connected_mt5: false,
    connected_signal_engine: false,
    version: '—',
    magic: 0,
  },
  trades:     [],
  riskGuards: [],
  metrics: {
    equity: 0,
    peak_equity: 0,
    drawdown_pct: 0,
    daily_pnl: 0,
    open_trades: 0,
    max_trades: 0,
    pending_signals: 0,
    total_trades_today: 0,
    winning_trades: 0,
    losing_trades: 0,
    win_rate: 0,
    signal_to_trade_ms: 0,
  },
  signals: [],
  logs:    [],
}

export function useEngineSocket(): [EngineState, SendCommand] {
  const [state, setState] = useState<EngineState>(EMPTY_STATE)
  const wsRef    = useRef<WebSocket | null>(null)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const sendCommand = useCallback<SendCommand>((type, payload = {}) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type, payload }))
    }
  }, [])

  useEffect(() => {
    let destroyed = false

    function connect() {
      if (destroyed) return
      const ws = new WebSocket(WS_URL)
      wsRef.current = ws

      ws.onopen = () =>
        setState(prev => ({ ...prev, connected: true, engine: { ...prev.engine, status: 'RUNNING' } }))

      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data as string) as { type: string; payload: unknown }
          setState(prev => applyMessage(prev, msg.type, msg.payload))
        } catch { /* ignore malformed */ }
      }

      ws.onclose = () => {
        setState(prev => ({
          ...prev,
          connected: false,
          engine: { ...prev.engine, status: 'CONNECTING' },
        }))
        if (!destroyed) timerRef.current = setTimeout(connect, 3000)
      }

      ws.onerror = () => ws.close()
    }

    connect()
    return () => {
      destroyed = true
      if (timerRef.current) clearTimeout(timerRef.current)
      wsRef.current?.close()
    }
  }, [])

  return [state, sendCommand]
}

function applyMessage(state: EngineState, type: string, payload: unknown): EngineState {
  switch (type) {
    case 'STATE_SNAPSHOT':
      return { ...(payload as EngineState), connected: true }

    case 'trade.opened':
      return { ...state, trades: [...state.trades, payload as EngineState['trades'][0]] }

    case 'trade.tp1_hit': {
      const { trade_id } = payload as { trade_id: string }
      return {
        ...state,
        trades: state.trades.map(t =>
          t.id === trade_id ? { ...t, state: 'PARTIALLY_CLOSED' as const } : t
        ),
      }
    }

    case 'trade.tp2_hit':
    case 'trade.sl_hit':
    case 'trade.closed': {
      const { trade_id } = payload as { trade_id: string }
      return { ...state, trades: state.trades.filter(t => t.id !== trade_id) }
    }

    case 'METRICS_UPDATE':
      return { ...state, metrics: { ...state.metrics, ...(payload as Partial<EngineState['metrics']>) } }

    case 'signal.received':
    case 'signal.approved':
    case 'signal.rejected':
    case 'signal.triggered':
    case 'signal.opened':
      return {
        ...state,
        signals: [payload as EngineState['signals'][0], ...state.signals].slice(0, 100),
      }

    case 'engine.paused':
      return { ...state, engine: { ...state.engine, status: 'PAUSED' } }

    case 'engine.resumed':
      return { ...state, engine: { ...state.engine, status: 'RUNNING' } }

    default:
      return state
  }
}
