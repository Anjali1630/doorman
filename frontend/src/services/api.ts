import type { Analytics, BrowserDom, BrowserState, Execution, Integration } from '../types'

const BASE = '/api'

async function req<T>(path: string, options?: RequestInit): Promise<T> {
  const resp = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!resp.ok) {
    const text = await resp.text()
    throw new Error(`${resp.status} ${resp.statusText}: ${text}`)
  }
  return resp.json()
}

export const api = {
  health: () => req<{ status: string; llm_mode: string; openrouter_model: string | null }>('/health'),

  runAgent: (text: string) =>
    req<Execution>('/agent/run', { method: 'POST', body: JSON.stringify({ text }) }),

  listExecutions: () => req<Execution[]>('/executions'),
  getExecution: (id: string) => req<Execution>(`/executions/${id}`),
  getExecutionTrace: (id: string) =>
    req<{ execution_id: string; status: string; steps: Execution['steps'] }>(`/executions/${id}/trace`),

  listIntegrations: () => req<Integration[]>('/integrations'),
  testIntegration: (id: string) =>
    req<{ ok: boolean; detail: string }>(`/integrations/${id}/test`, { method: 'POST' }),

  runEvaluation: () =>
    req<{ id: string; metrics: any; results: any[] }>('/evaluations/run', { method: 'POST' }),
  listEvaluations: () => req<any[]>('/evaluations'),

  analytics: () => req<Analytics>('/analytics'),

  browserState: (executionId?: string) =>
    req<BrowserState>(`/browser/state${executionId ? `?execution_id=${executionId}` : ''}`),
  browserDom: (executionId?: string) =>
    req<BrowserDom>(`/browser/dom${executionId ? `?execution_id=${executionId}` : ''}`),

  demoLoad: (reset: boolean) =>
    req<{ status: string; demo_site_url: string; demo_credentials: any; sample_tasks: string[] }>(
      '/demo/load',
      { method: 'POST', body: JSON.stringify({ reset }) },
    ),
  demoReset: () => req<{ status: string }>('/demo/reset', { method: 'POST' }),
}
