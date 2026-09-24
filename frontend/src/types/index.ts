export interface ExecutionStep {
  id: string
  step_label: string
  action: string | null
  target: Record<string, any> | null
  tool: string | null
  execution_method: 'api' | 'browser' | 'planning' | 'replanning' | null
  validation_result: string | null
  status: 'success' | 'failed' | 'skipped'
  duration_ms: number
  error: string | null
  retry_count: number
  timestamp: string
}

export interface Execution {
  id: string
  task_id: string
  plan_id: string | null
  status: string
  strategy: string
  started_at: string
  finished_at: string | null
  success: boolean | null
  result: Record<string, any> | null
  error: string | null
  retries_used: number
  replans_used: number
  api_calls: number
  browser_actions: number
  task_text: string | null
  steps?: ExecutionStep[]
}

export interface Integration {
  id: string
  name: string
  kind: string
  status: string
  capabilities: string[]
  last_tested_at: string | null
  last_test_result: string | null
}

export interface EvaluationMetrics {
  task_success_rate_pct: number
  adversarial_pass_rate_pct: number
  false_action_rate_pct: number
  validation_rejection_rate_pct: number
  recovery_rate_pct: number
  average_steps_per_task: number
  average_execution_time_ms: number
  total_demo_tasks: number
  total_adversarial_cases: number
}

export interface EvaluationResultCase {
  case_name: string
  case_kind: 'demo_task' | 'adversarial'
  success: boolean
  unsafe_action_blocked: boolean
  recovered: boolean
  steps_taken: number
  duration_ms: number
  detail: string | null
}

export interface Analytics {
  total_executions: number
  completed: number
  failed: number
  success_rate_pct: number
  total_api_calls: number
  total_browser_actions: number
  api_usage_rate_pct: number
  total_retries: number
  total_replans: number
  llm_mode: string
}

export interface BrowserState {
  available: boolean
  url?: string
  title?: string
  element_count?: number
  timestamp?: string
  detail?: string
}

export interface BrowserDom {
  available: boolean
  url?: string
  title?: string
  elements: Array<{
    role: string
    name: string
    visible: boolean
    enabled: boolean
    test_id: string | null
    href: string | null
  }>
}
