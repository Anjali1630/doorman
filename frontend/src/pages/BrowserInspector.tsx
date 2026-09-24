import { useEffect, useState } from 'react'
import { api } from '../services/api'
import type { BrowserDom, BrowserState } from '../types'

export default function BrowserInspector() {
  const [state, setState] = useState<BrowserState | null>(null)
  const [dom, setDom] = useState<BrowserDom | null>(null)
  const [selected, setSelected] = useState<number | null>(null)

  const load = () => {
    api.browserState().then(setState)
    api.browserDom().then(setDom)
  }

  useEffect(() => { load() }, [])

  const el = selected !== null ? dom?.elements[selected] : null

  return (
    <div className="p-8 max-w-[1400px] mx-auto">
      <header className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Browser Inspector</h1>
          <p className="text-sm text-[var(--color-muted)] mt-1">
            The last real DOM snapshot the validator checked planned actions against.
          </p>
        </div>
        <button onClick={load} className="text-xs px-3 py-1.5 rounded border border-[var(--color-border)] hover:border-[var(--color-accent)]">
          Refresh
        </button>
      </header>

      {!state?.available && (
        <div className="text-center text-sm text-[var(--color-muted)] py-16 border border-dashed border-[var(--color-border)] rounded-lg">
          No browser observation recorded yet. Run a task from the Control Center first.
        </div>
      )}

      {state?.available && (
        <div className="grid grid-cols-3 gap-5">
          <section className="col-span-2 border border-[var(--color-border)] bg-[var(--color-surface)] rounded-lg p-4">
            <div className="mb-3 text-sm">
              <div className="text-[var(--color-muted)]">URL</div>
              <div className="mono">{state.url}</div>
              <div className="text-[var(--color-muted)] mt-2">Page title</div>
              <div>{state.title}</div>
            </div>
            <h2 className="text-xs uppercase tracking-wide text-[var(--color-muted)] mb-2 mt-4">
              Interactive Elements ({dom?.elements.length ?? 0})
            </h2>
            <div className="max-h-[440px] overflow-y-auto border border-[var(--color-border)] rounded-md">
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-[var(--color-surface-2)]">
                  <tr className="text-left text-xs text-[var(--color-muted)]">
                    <th className="px-3 py-2">Role</th>
                    <th className="px-3 py-2">Name</th>
                    <th className="px-3 py-2">Visible</th>
                    <th className="px-3 py-2">Enabled</th>
                  </tr>
                </thead>
                <tbody>
                  {dom?.elements.map((e, i) => (
                    <tr
                      key={i}
                      onClick={() => setSelected(i)}
                      className={`cursor-pointer border-t border-[var(--color-border)] ${selected === i ? 'bg-[var(--color-surface-2)]' : 'hover:bg-[var(--color-surface-2)]/50'}`}
                    >
                      <td className="px-3 py-1.5 mono text-xs">{e.role}</td>
                      <td className="px-3 py-1.5 truncate max-w-[220px]">{e.name || <span className="text-[var(--color-muted)]">(no accessible name)</span>}</td>
                      <td className="px-3 py-1.5">{e.visible ? 'yes' : 'no'}</td>
                      <td className="px-3 py-1.5">{e.enabled ? 'yes' : 'no'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="border border-[var(--color-border)] bg-[var(--color-surface)] rounded-lg p-4">
            <h2 className="text-xs uppercase tracking-wide text-[var(--color-muted)] mb-3">Element Details</h2>
            {!el && <p className="text-sm text-[var(--color-muted)]">Select an element from the table.</p>}
            {el && (
              <dl className="space-y-3 text-sm">
                <div><dt className="text-[var(--color-muted)] text-xs">ROLE</dt><dd className="mono">{el.role}</dd></div>
                <div><dt className="text-[var(--color-muted)] text-xs">NAME</dt><dd>{el.name || '—'}</dd></div>
                <div><dt className="text-[var(--color-muted)] text-xs">VISIBLE</dt><dd>{el.visible ? 'YES' : 'NO'}</dd></div>
                <div><dt className="text-[var(--color-muted)] text-xs">ENABLED</dt><dd>{el.enabled ? 'YES' : 'NO'}</dd></div>
                <div><dt className="text-[var(--color-muted)] text-xs">TARGETING STRATEGY</dt>
                  <dd className="mono text-xs">{el.test_id ? 'test_id' : 'role + accessible name'}</dd></div>
                {el.href && <div><dt className="text-[var(--color-muted)] text-xs">HREF</dt><dd className="mono text-xs truncate">{el.href}</dd></div>}
              </dl>
            )}
          </section>
        </div>
      )}
    </div>
  )
}
