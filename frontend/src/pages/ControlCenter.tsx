import { useState } from 'react'
import { Play, Loader2, ChevronRight, AlertCircle } from 'lucide-react'
import { api } from '../services/api'
import type { Execution } from '../types'
import { MethodTag, RoutingDiagram, StatusPill } from '../components/Widgets'

const SAMPLE_TASKS = [
  'Log into the website and download the latest invoice.',
  'Find the latest invoice and tell me its invoice number and amount.',
  'Find all unpaid invoices above ₹10,000.',
  'Open the latest unpaid invoice and download it.',
  'Find the customer associated with the latest invoice and return their email.',
  'Record a payment of ₹500 for invoice INV-1005',
  'How much is still pending for invoice INV-1005?',
  'Show me all partially paid invoices',
]

export default function ControlCenter() {
  const [text, setText] = useState('')
  const [running, setRunning] = useState(false)
  const [execution, setExecution] = useState<Execution | null>(null)
  const [errorBanner, setErrorBanner] = useState<string | null>(null)

  const run = async (taskText: string) => {
    if (!taskText.trim() || running) return
    setRunning(true)
    setErrorBanner(null)
    try {
      const result = await api.runAgent(taskText)
      setExecution(result)
    } catch (e: any) {
      setErrorBanner(e.message || 'Agent run failed')
    } finally {
      setRunning(false)
    }
  }

  const steps = execution?.steps ?? []
  const planningSteps = steps.filter((s) => s.execution_method === 'planning' || s.execution_method === 'replanning')

  return (
    <div className="p-8 max-w-[1400px] mx-auto">
      <header className="mb-8">
        <h1 className="text-xl font-semibold tracking-tight">Browser Automation Agent</h1>
        <p className="text-sm text-[var(--color-muted)] mt-1">
          Describe a task in plain language. The agent checks for a direct API first, and falls back to
          real browser automation only when it needs to.
        </p>
      </header>

      <div className="border border-[var(--color-border)] bg-[var(--color-surface)] rounded-lg p-4 mb-6">
        <div className="flex gap-2">
          <input
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && run(text)}
            placeholder="What do you want me to automate?"
            className="flex-1 bg-[var(--color-surface-2)] border border-[var(--color-border)] rounded-md px-3 py-2 text-sm outline-none focus:border-[var(--color-accent)]"
          />
          <button
            onClick={() => run(text)}
            disabled={running}
            className="flex items-center gap-2 px-4 py-2 rounded-md bg-[var(--color-accent)] text-[#06070a] text-sm font-medium disabled:opacity-50"
          >
            {running ? <Loader2 size={15} className="animate-spin" /> : <Play size={15} />}
            Run Agent
          </button>
        </div>
        <div className="flex flex-wrap gap-2 mt-3">
          {SAMPLE_TASKS.map((t) => (
            <button
              key={t}
              onClick={() => { setText(t); run(t) }}
              disabled={running}
              className="text-xs px-2.5 py-1 rounded border border-[var(--color-border)] text-[var(--color-muted)] hover:text-[var(--color-text)] hover:border-[var(--color-accent)] transition-colors disabled:opacity-50"
            >
              {t}
            </button>
          ))}
        </div>
      </div>

      {errorBanner && (
        <div className="mb-6 flex items-center gap-2 text-sm text-[var(--color-danger)] border border-[var(--color-danger)]/40 bg-[var(--color-danger)]/10 rounded-md px-3 py-2">
          <AlertCircle size={15} /> {errorBanner}
        </div>
      )}

      {execution && (
        <>
          <div className="flex items-center gap-4 mb-6 text-sm">
            <StatusPill status={execution.status} />
            <span className="text-[var(--color-muted)]">Strategy: <span className="mono text-[var(--color-text)]">{execution.strategy}</span></span>
            <span className="text-[var(--color-muted)]">API calls: <span className="mono text-[var(--color-accent-api)]">{execution.api_calls}</span></span>
            <span className="text-[var(--color-muted)]">Browser actions: <span className="mono text-[var(--color-accent-browser)]">{execution.browser_actions}</span></span>
            <span className="text-[var(--color-muted)]">Retries: <span className="mono">{execution.retries_used}</span></span>
            <span className="text-[var(--color-muted)]">Re-plans: <span className="mono">{execution.replans_used}</span></span>
          </div>

          <div className="grid grid-cols-3 gap-5">
            {/* LEFT: Agent brain / plan */}
            <section className="border border-[var(--color-border)] bg-[var(--color-surface)] rounded-lg p-4">
              <h2 className="text-xs uppercase tracking-wide text-[var(--color-muted)] mb-3">Agent Reasoning</h2>
              <ol className="space-y-2">
                {planningSteps.map((s) => (
                  <li key={s.id} className="flex items-start gap-2 text-sm">
                    <ChevronRight size={14} className="mt-0.5 text-[var(--color-accent)] shrink-0" />
                    <span>{s.step_label}</span>
                  </li>
                ))}
              </ol>
              {execution.result?.clarification_options && (
                <div className="mt-4 pt-4 border-t border-[var(--color-border)]">
                  <p className="text-sm text-[var(--color-accent-browser)] mb-2">Needs clarification — pick one:</p>
                  <div className="space-y-1.5">
                    {execution.result.clarification_options.map((opt: string) => (
                      <button
                        key={opt}
                        onClick={() => { setText(opt); run(opt) }}
                        className="block w-full text-left text-xs px-2.5 py-1.5 rounded border border-[var(--color-border)] hover:border-[var(--color-accent)]"
                      >
                        {opt}
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </section>

            {/* CENTER: routing diagram + result */}
            <section className="border border-[var(--color-border)] bg-[var(--color-surface)] rounded-lg p-4">
              <h2 className="text-xs uppercase tracking-wide text-[var(--color-muted)] mb-3">API-First Routing</h2>
              <RoutingDiagram execution={execution} />
              {execution.result && !execution.result.clarification_options && (
                <div className="mt-2 pt-3 border-t border-[var(--color-border)]">
                  <h3 className="text-xs uppercase tracking-wide text-[var(--color-muted)] mb-2">Result</h3>
                  <pre className="mono text-xs bg-[var(--color-surface-2)] rounded p-3 overflow-x-auto">
                    {JSON.stringify(execution.result, null, 2)}
                  </pre>
                </div>
              )}
              {execution.error && (
                <div className="mt-2 pt-3 border-t border-[var(--color-border)] text-sm text-[var(--color-danger)]">
                  {execution.error}
                </div>
              )}
            </section>

            {/* RIGHT: execution trace */}
            <section className="border border-[var(--color-border)] bg-[var(--color-surface)] rounded-lg p-4 max-h-[560px] overflow-y-auto">
              <h2 className="text-xs uppercase tracking-wide text-[var(--color-muted)] mb-3">Execution Trace</h2>
              <ol className="space-y-2.5">
                {steps.map((s) => (
                  <li key={s.id} className="text-sm border-l-2 pl-3" style={{
                    borderColor: s.status === 'failed' ? 'var(--color-danger)' :
                      s.status === 'success' ? 'var(--color-border)' : 'var(--color-border)',
                  }}>
                    <div className="flex items-center justify-between gap-2">
                      <span className={s.status === 'failed' ? 'text-[var(--color-danger)]' : ''}>{s.step_label}</span>
                      <MethodTag method={s.execution_method} />
                    </div>
                    <div className="text-[11px] text-[var(--color-muted)] mono mt-0.5">
                      {s.duration_ms > 0 ? `${s.duration_ms.toFixed(0)}ms` : ''}
                      {s.validation_result ? ` · validation: ${s.validation_result}` : ''}
                      {s.retry_count ? ` · retry ${s.retry_count}` : ''}
                    </div>
                  </li>
                ))}
              </ol>
            </section>
          </div>
        </>
      )}

      {!execution && !running && (
        <div className="text-center text-sm text-[var(--color-muted)] py-16 border border-dashed border-[var(--color-border)] rounded-lg">
          Run a task above, or pick one of the sample tasks, to see the plan, routing, and live execution trace.
        </div>
      )}
    </div>
  )
}
