import type { Execution } from '../types'

export function StatusPill({ status }: { status: string }) {
  const map: Record<string, string> = {
    COMPLETED: 'text-[var(--color-success)] border-[var(--color-success)]/40 bg-[var(--color-success)]/10',
    FAILED: 'text-[var(--color-danger)] border-[var(--color-danger)]/40 bg-[var(--color-danger)]/10',
    WAITING_FOR_USER: 'text-[var(--color-accent-browser)] border-[var(--color-accent-browser)]/40 bg-[var(--color-accent-browser)]/10',
    EXECUTING: 'text-[var(--color-accent)] border-[var(--color-accent)]/40 bg-[var(--color-accent)]/10',
    PLANNING: 'text-[var(--color-accent)] border-[var(--color-accent)]/40 bg-[var(--color-accent)]/10',
  }
  return (
    <span className={`text-[11px] px-2 py-0.5 rounded border mono ${map[status] || 'text-[var(--color-muted)] border-[var(--color-border)]'}`}>
      {status}
    </span>
  )
}

export function MethodTag({ method }: { method: string | null }) {
  if (!method) return <span className="text-[var(--color-muted)]">—</span>
  const styles: Record<string, string> = {
    api: 'text-[var(--color-accent-api)]',
    browser: 'text-[var(--color-accent-browser)]',
    planning: 'text-[var(--color-accent)]',
    replanning: 'text-[var(--color-danger)]',
  }
  return <span className={`mono text-xs ${styles[method] || 'text-[var(--color-muted)]'}`}>{method.toUpperCase()}</span>
}

export function RoutingDiagram({ execution }: { execution: Execution | null }) {
  const usedApi = (execution?.api_calls ?? 0) > 0
  const usedBrowser = (execution?.browser_actions ?? 0) > 0
  const replanned = (execution?.replans_used ?? 0) > 0

  const activeApi = usedApi
  const activeBrowser = usedBrowser

  return (
    <svg viewBox="0 0 460 200" className="w-full h-auto">
      <defs>
        <marker id="arrow" markerWidth="8" markerHeight="8" refX="6" refY="4" orient="auto">
          <path d="M0,0 L8,4 L0,8 z" fill="var(--color-border)" />
        </marker>
      </defs>

      {/* Task node */}
      <rect x="170" y="6" width="120" height="30" rx="4" fill="var(--color-surface-2)" stroke="var(--color-border)" />
      <text x="230" y="25" textAnchor="middle" className="mono" fontSize="11" fill="var(--color-text)">USER TASK</text>

      <line x1="230" y1="36" x2="230" y2="56" stroke="var(--color-border)" markerEnd="url(#arrow)" />

      {/* Router node */}
      <rect x="150" y="58" width="160" height="30" rx="4" fill="var(--color-surface-2)" stroke="var(--color-accent)" />
      <text x="230" y="77" textAnchor="middle" className="mono" fontSize="11" fill="var(--color-text)">EXECUTION ROUTER</text>

      {/* Branch lines */}
      <line x1="200" y1="88" x2="100" y2="112" stroke={activeApi ? 'var(--color-accent-api)' : 'var(--color-border)'} strokeWidth={activeApi ? 2 : 1} markerEnd="url(#arrow)" />
      <line x1="260" y1="88" x2="360" y2="112" stroke={activeBrowser ? 'var(--color-accent-browser)' : 'var(--color-border)'} strokeWidth={activeBrowser ? 2 : 1} markerEnd="url(#arrow)" />

      {/* API path */}
      <rect x="20" y="114" width="160" height="30" rx="4"
            fill={activeApi ? 'rgba(79,209,197,0.12)' : 'var(--color-surface-2)'}
            stroke={activeApi ? 'var(--color-accent-api)' : 'var(--color-border)'} />
      <text x="100" y="133" textAnchor="middle" className="mono" fontSize="10" fill={activeApi ? 'var(--color-accent-api)' : 'var(--color-muted)'}>DIRECT API CALL</text>

      {/* Browser path */}
      <rect x="280" y="114" width="160" height="30" rx="4"
            fill={activeBrowser ? 'rgba(240,168,87,0.12)' : 'var(--color-surface-2)'}
            stroke={activeBrowser ? 'var(--color-accent-browser)' : 'var(--color-border)'} />
      <text x="360" y="133" textAnchor="middle" className="mono" fontSize="10" fill={activeBrowser ? 'var(--color-accent-browser)' : 'var(--color-muted)'}>PLAYWRIGHT BROWSER</text>

      <line x1="100" y1="144" x2="100" y2="160" stroke={activeApi ? 'var(--color-accent-api)' : 'var(--color-border)'} markerEnd="url(#arrow)" />
      <line x1="360" y1="144" x2="360" y2="160" stroke={activeBrowser ? 'var(--color-accent-browser)' : 'var(--color-border)'} markerEnd="url(#arrow)" />

      <rect x="30" y="162" width="140" height="28" rx="4" fill="var(--color-surface-2)" stroke="var(--color-border)" />
      <text x="100" y="180" textAnchor="middle" className="mono" fontSize="10" fill="var(--color-muted)">VALIDATE + RESULT</text>

      <rect x="290" y="162" width="140" height="28" rx="4" fill="var(--color-surface-2)" stroke="var(--color-border)" />
      <text x="360" y="180" textAnchor="middle" className="mono" fontSize="10" fill="var(--color-muted)">VALIDATE + RESULT</text>

      {replanned && (
        <text x="230" y="105" textAnchor="middle" className="mono" fontSize="9" fill="var(--color-danger)">re-planned ↴</text>
      )}
    </svg>
  )
}
