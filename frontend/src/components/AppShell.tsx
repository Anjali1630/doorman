import { NavLink, Outlet } from 'react-router-dom'
import {
  LayoutGrid, Search, ListTree, Plug, Gauge, Radar,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../services/api'

const NAV = [
  { to: '/', label: 'Control Center', icon: LayoutGrid, end: true },
  { to: '/inspector', label: 'Browser Inspector', icon: Search },
  { to: '/executions', label: 'Executions', icon: ListTree },
  { to: '/integrations', label: 'Integrations', icon: Plug },
  { to: '/evaluation', label: 'Evaluation', icon: Gauge },
]

export default function AppShell() {
  const [llmMode, setLlmMode] = useState<string | null>(null)

  useEffect(() => {
    api.health().then((h) => setLlmMode(h.llm_mode)).catch(() => setLlmMode('unreachable'))
  }, [])

  return (
    <div className="flex h-full">
      <aside className="w-60 shrink-0 border-r border-[var(--color-border)] bg-[var(--color-surface)] flex flex-col">
        <div className="px-5 py-5 border-b border-[var(--color-border)]">
          <div className="flex items-center gap-2">
            <Radar size={20} className="text-[var(--color-accent)]" strokeWidth={2} />
            <span className="font-semibold tracking-tight text-[15px]">doorman</span>
          </div>
          <p className="text-xs text-[var(--color-muted)] mt-1 leading-snug">
            API-first browser automation agent
          </p>
        </div>
        <nav className="flex-1 px-3 py-4 space-y-1">
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                `flex items-center gap-2.5 px-3 py-2 rounded-md text-sm transition-colors ${
                  isActive
                    ? 'bg-[var(--color-surface-2)] text-[var(--color-text)]'
                    : 'text-[var(--color-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-surface-2)]/60'
                }`
              }
            >
              <Icon size={16} strokeWidth={2} />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="px-4 py-4 border-t border-[var(--color-border)]">
          <div className="text-[11px] text-[var(--color-muted)] mb-1">LLM MODE</div>
          <div className="flex items-center gap-2">
            <span
              className={`h-1.5 w-1.5 rounded-full ${
                llmMode === 'openrouter' ? 'bg-[var(--color-accent-api)]' : 'bg-[var(--color-accent-browser)]'
              }`}
            />
            <span className="text-sm mono">
              {llmMode === 'openrouter' ? 'OpenRouter' : llmMode === 'deterministic_fallback' ? 'Local Fallback' : '—'}
            </span>
          </div>
        </div>
      </aside>
      <main className="flex-1 overflow-y-auto">
        <Outlet />
      </main>
    </div>
  )
}
