import { useEffect, useState } from 'react'
import { api } from '../services/api'
import type { Execution } from '../types'
import { MethodTag, StatusPill } from '../components/Widgets'

export default function Executions() {
  const [executions, setExecutions] = useState<Execution[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<Execution | null>(null)

  useEffect(() => { api.listExecutions().then(setExecutions) }, [])

  useEffect(() => {
    if (selectedId) api.getExecution(selectedId).then(setDetail)
  }, [selectedId])

  return (
    <div className="p-8 max-w-[1400px] mx-auto">
      <header className="mb-6">
        <h1 className="text-xl font-semibold tracking-tight">Executions</h1>
        <p className="text-sm text-[var(--color-muted)] mt-1">Every task run, with its full trace.</p>
      </header>

      {executions.length === 0 ? (
        <div className="text-center text-sm text-[var(--color-muted)] py-16 border border-dashed border-[var(--color-border)] rounded-lg">
          No executions yet. Run a task from the Control Center.
        </div>
      ) : (
        <div className="grid grid-cols-5 gap-5">
          <div className="col-span-2 border border-[var(--color-border)] rounded-lg overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-[var(--color-surface-2)] text-xs text-[var(--color-muted)]">
                <tr className="text-left">
                  <th className="px-3 py-2">Task</th>
                  <th className="px-3 py-2">Status</th>
                  <th className="px-3 py-2">API</th>
                  <th className="px-3 py-2">Browser</th>
                </tr>
              </thead>
              <tbody>
                {executions.map((e) => (
                  <tr
                    key={e.id}
                    onClick={() => setSelectedId(e.id)}
                    className={`cursor-pointer border-t border-[var(--color-border)] ${selectedId === e.id ? 'bg-[var(--color-surface-2)]' : 'hover:bg-[var(--color-surface-2)]/50'}`}
                  >
                    <td className="px-3 py-2 truncate max-w-[220px]">{e.task_text}</td>
                    <td className="px-3 py-2"><StatusPill status={e.status} /></td>
                    <td className="px-3 py-2 mono text-xs">{e.api_calls}</td>
                    <td className="px-3 py-2 mono text-xs">{e.browser_actions}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="col-span-3 border border-[var(--color-border)] bg-[var(--color-surface)] rounded-lg p-4 max-h-[600px] overflow-y-auto">
            {!detail ? (
              <p className="text-sm text-[var(--color-muted)]">Select an execution to view its trace.</p>
            ) : (
              <>
                <h2 className="text-sm font-medium mb-1">{detail.task_text}</h2>
                <div className="flex items-center gap-3 mb-4 text-xs text-[var(--color-muted)]">
                  <StatusPill status={detail.status} />
                  <span>retries: {detail.retries_used}</span>
                  <span>re-plans: {detail.replans_used}</span>
                </div>
                <ol className="space-y-2.5">
                  {detail.steps?.map((s) => (
                    <li key={s.id} className="text-sm border-l-2 border-[var(--color-border)] pl-3">
                      <div className="flex items-center justify-between gap-2">
                        <span className={s.status === 'failed' ? 'text-[var(--color-danger)]' : ''}>{s.step_label}</span>
                        <MethodTag method={s.execution_method} />
                      </div>
                      <div className="text-[11px] text-[var(--color-muted)] mono mt-0.5">
                        {s.duration_ms > 0 ? `${s.duration_ms.toFixed(0)}ms` : ''}
                        {s.error ? ` · ${s.error}` : ''}
                      </div>
                    </li>
                  ))}
                </ol>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
