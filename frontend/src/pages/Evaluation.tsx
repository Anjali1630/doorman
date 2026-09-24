import { useState } from 'react'
import { Loader2, PlayCircle } from 'lucide-react'
import { api } from '../services/api'
import type { EvaluationMetrics, EvaluationResultCase } from '../types'

const METRIC_LABELS: Record<keyof EvaluationMetrics, string> = {
  task_success_rate_pct: 'Task Success Rate',
  adversarial_pass_rate_pct: 'Adversarial Pass Rate',
  false_action_rate_pct: 'False Action Rate',
  validation_rejection_rate_pct: 'Validation Rejection Rate',
  recovery_rate_pct: 'Recovery Rate',
  average_steps_per_task: 'Avg Steps / Task',
  average_execution_time_ms: 'Avg Execution Time (ms)',
  total_demo_tasks: 'Demo Tasks Run',
  total_adversarial_cases: 'Adversarial Cases Run',
}

export default function Evaluation() {
  const [running, setRunning] = useState(false)
  const [metrics, setMetrics] = useState<EvaluationMetrics | null>(null)
  const [results, setResults] = useState<EvaluationResultCase[]>([])

  const run = async () => {
    setRunning(true)
    try {
      const res = await api.runEvaluation()
      setMetrics(res.metrics)
      setResults(res.results)
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="p-8 max-w-[1200px] mx-auto">
      <header className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Agent Evaluation</h1>
          <p className="text-sm text-[var(--color-muted)] mt-1">
            Runs the 5 demo tasks and 6 adversarial hallucination probes for real, and computes every metric from the results.
          </p>
        </div>
        <button
          onClick={run}
          disabled={running}
          className="flex items-center gap-2 px-4 py-2 rounded-md bg-[var(--color-accent)] text-[#06070a] text-sm font-medium disabled:opacity-50"
        >
          {running ? <Loader2 size={15} className="animate-spin" /> : <PlayCircle size={15} />}
          Run Evaluation Suite
        </button>
      </header>

      {!metrics && !running && (
        <div className="text-center text-sm text-[var(--color-muted)] py-16 border border-dashed border-[var(--color-border)] rounded-lg">
          No evaluation run yet. Click "Run Evaluation Suite" — this takes a few seconds and exercises the real agent.
        </div>
      )}

      {metrics && (
        <>
          <div className="grid grid-cols-4 gap-4 mb-8">
            {(Object.keys(METRIC_LABELS) as (keyof EvaluationMetrics)[]).map((key) => (
              <div key={key} className="border border-[var(--color-border)] bg-[var(--color-surface)] rounded-lg p-4">
                <div className="text-2xl font-semibold mono">{metrics[key]}{key.endsWith('_pct') ? '%' : ''}</div>
                <div className="text-xs text-[var(--color-muted)] mt-1">{METRIC_LABELS[key]}</div>
              </div>
            ))}
          </div>

          <h2 className="text-xs uppercase tracking-wide text-[var(--color-muted)] mb-3">Case Results</h2>
          <div className="border border-[var(--color-border)] rounded-lg overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-[var(--color-surface-2)] text-xs text-[var(--color-muted)]">
                <tr className="text-left">
                  <th className="px-3 py-2">Case</th>
                  <th className="px-3 py-2">Kind</th>
                  <th className="px-3 py-2">Success</th>
                  <th className="px-3 py-2">Blocked</th>
                  <th className="px-3 py-2">Recovered</th>
                  <th className="px-3 py-2">Detail</th>
                </tr>
              </thead>
              <tbody>
                {results.map((r, i) => (
                  <tr key={i} className="border-t border-[var(--color-border)]">
                    <td className="px-3 py-2">{r.case_name}</td>
                    <td className="px-3 py-2 mono text-xs">{r.case_kind}</td>
                    <td className={`px-3 py-2 ${r.success ? 'text-[var(--color-success)]' : 'text-[var(--color-danger)]'}`}>
                      {r.success ? 'pass' : 'fail'}
                    </td>
                    <td className="px-3 py-2">{r.unsafe_action_blocked ? 'yes' : '—'}</td>
                    <td className="px-3 py-2">{r.recovered ? 'yes' : '—'}</td>
                    <td className="px-3 py-2 text-xs text-[var(--color-muted)] max-w-[280px] truncate">{r.detail}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}
