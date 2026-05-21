export type TradeSide    = 'BUY' | 'SELL'
export type TradeState   = 'OPEN' | 'PARTIALLY_CLOSED'
export type EngineStatus = 'RUNNING' | 'STOPPED' | 'PAUSED' | 'CONNECTING'
export type GuardStatus  = 'ACTIVE' | 'PAUSED' | 'DISABLED'
export type SignalStatus = 'RECEIVED' | 'APPROVED' | 'REJECTED' | 'TRIGGERED' | 'OPENED'

export interface Trade {
  id: string
  symbol: string
  side: TradeSide
  state: TradeState
  entry_price: number
  current_price: number
  sl: number
  tp1: number
  tp2: number
  lots: number
  tp1_lots: number
  pnl: number
  opened_at: string
  duration_sec: number
  ticket?: number
}

export interface RiskGuard {
  id: 'guard1' | 'guard2' | 'guard3'
  name: string
  description: string
  status: GuardStatus
  current_value: number
  threshold: number
  unit: '%'
}

export interface Metrics {
  equity: number
  peak_equity: number
  drawdown_pct: number
  daily_pnl: number
  open_trades: number
  max_trades: number
  pending_signals: number
  total_trades_today: number
  winning_trades: number
  losing_trades: number
  win_rate: number
  signal_to_trade_ms: number
}

export interface EngineInfo {
  status: EngineStatus
  uptime_sec: number
  mode: string
  connected_mt5: boolean
  connected_signal_engine: boolean
  version: string
  magic: number
}

export interface SignalEvent {
  id: string
  symbol: string
  direction: TradeSide
  status: SignalStatus
  timestamp: string
  reason?: string
}

export interface LogEntry {
  ts: number
  level: 'INFO' | 'WARN' | 'WARNING' | 'ERROR' | 'DEBUG'
  name: string
  msg: string
}

export interface EngineState {
  engine: EngineInfo
  trades: Trade[]
  riskGuards: RiskGuard[]
  metrics: Metrics
  signals: SignalEvent[]
  logs: LogEntry[]
  connected: boolean
}
