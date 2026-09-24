import { useEffect, useState } from 'react'
import { CheckCircle2, XCircle, HelpCircle, Loader2 } from 'lucide-react'
import { api } from '../services/api'
import type { Integration } from '../types'

const STATUS_ICON: Record<string, any> = {
  connected: CheckCircle2,
  available: CheckCircle2,
  active: CheckCircle2,
  unavailable: XCircle,
  error: XCircle,
  no_api_key: HelpCircle,
}

export default function Integrations() {
  const [integrations, setIntegrations] = useState<Integration[]>([])
  const [testing, setTesting] = useState<string | null>(null)

  const load = () => api.listIntegrations().then(setIntegrations)
  useEffect(() => { load() }, [])

  const runTest = async (id: string) => {
    setTesting(id)
    try {
      await api.testIntegration(id)
    } finally {
      setTesting(null)
      load()
    }
  }

  return (
    <div className="p-8 max-w-[1000px] mx-auto">
      <header className="mb-6">
        <h1 className="text-xl font-semibold tracking-tight">Integrations</h1>
        <p className="text-sm text-[var(--color-muted)] mt-1">
          The three moving parts the agent can call on: the LLM, the Invoice API, and Playwright.
        </p>
      </header>

      <div className="space-y-3">
        {integrations.map((i) => {
          const Icon = STATUS_ICON[i.status] || HelpCircle
          return (
            <div key={i.id} className="border border-[var(--color-border)] bg-[var(--color-surface)] rounded-lg p-4 flex items-start justify-between">
              <div className="flex gap-3">
                <Icon size={18} className={
                  i.status === 'error' || i.status === 'unavailable' ? 'text-[var(--color-danger)] mt-0.5' :
                  i.status === 'no_api_key' ? 'text-[var(--color-accent-browser)] mt-0.5' : 'text-[var(--color-success)] mt-0.5'
                } />
                <div>
                  <div className="font-medium text-sm">{i.name}</div>
                  <div className="text-xs text-[var(--color-muted)] mt-0.5 mono">{i.status}</div>
                  <div className="flex flex-wrap gap-1.5 mt-2">
                    {i.capabilities.map((c) => (
                      <span key={c} className="text-[11px] px-1.5 py-0.5 rounded border border-[var(--color-border)] text-[var(--color-muted)] mono">{c}</span>
                    ))}
                  </div>
                  {i.last_test_result && (
                    <div className="text-xs text-[var(--color-muted)] mt-2">{i.last_test_result}</div>
                  )}
                </div>
              </div>
              <button
                onClick={() => runTest(i.id)}
                disabled={testing === i.id}
                className="text-xs px-3 py-1.5 rounded border border-[var(--color-border)] hover:border-[var(--color-accent)] flex items-center gap-1.5 shrink-0"
              >
                {testing === i.id && <Loader2 size={12} className="animate-spin" />}
                Test Connection
              </button>
            </div>
          )
        })}
      </div>
    </div>
  )
}
