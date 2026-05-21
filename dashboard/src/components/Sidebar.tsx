import { LayoutDashboard, BarChart2, Shield, Zap, Terminal, Settings2 } from 'lucide-react'
import type { NavPage } from '../App'

interface NavItem { id: NavPage; label: string; Icon: React.ElementType }

const NAV: NavItem[] = [
  { id: 'overview', label: 'OVERVIEW', Icon: LayoutDashboard },
  { id: 'trades',   label: 'TRADES',   Icon: BarChart2 },
  { id: 'risk',     label: 'RISK',     Icon: Shield },
  { id: 'signals',  label: 'SIGNALS',  Icon: Zap },
  { id: 'logs',     label: 'LOGS',     Icon: Terminal },
  { id: 'config',   label: 'CONFIG',   Icon: Settings2 },
]

interface Props {
  activePage: NavPage
  onNavigate: (page: NavPage) => void
}

export function Sidebar({ activePage, onNavigate }: Props) {
  return (
    <aside className="w-[148px] shrink-0 flex flex-col border-r border-white/[0.07] bg-surface overflow-hidden">
      {/* Nav */}
      <nav className="flex flex-col gap-px p-2 flex-1 pt-3">
        {NAV.map(({ id, label, Icon }) => {
          const active = activePage === id
          return (
            <button
              key={id}
              onClick={() => onNavigate(id)}
              className={[
                'flex items-center gap-2.5 px-2.5 py-[7px] rounded-md text-left transition-all duration-100',
                active
                  ? 'bg-raised border border-white/[0.13] text-ink-primary active-glow'
                  : 'border border-transparent text-ink-muted hover:text-ink-secondary hover:bg-white/[0.03]',
              ].join(' ')}
            >
              <Icon size={12} strokeWidth={active ? 2 : 1.5} />
              <span className="font-mono text-[9px] font-medium tracking-[0.14em]">{label}</span>
            </button>
          )
        })}
      </nav>

      {/* Footer */}
      <div className="p-3 border-t border-white/[0.06]">
        <div className="section-label mb-0.5">SYSTEM</div>
        <div className="font-mono text-[8px] text-ink-dim">© 2026 EX-ENG</div>
      </div>
    </aside>
  )
}
