import api from '../api'

export interface Strategy {
  id: string
  name: string
  description: string
  strategy_type: string
  selector_type: string
  allocator_type: string
  status: string
  config_path: string
  created_at: string
  updated_at: string
}

export interface StrategySignal {
  id: number
  run_id: number
  ts_code: string
  name: string
  industry: string
  signal: string
  score: number | null
  pb: number | null
  pe_ttm: number | null
  pb_anchor: number | null
  pb_ratio: number | null
  rise_from_1y_low_pct: number | null
  reason_code: string
  reason_text: string
  explain?: Record<string, unknown>
}

export interface StrategyDecision {
  id: number
  run_id: number
  ts_code: string
  name: string
  industry: string
  action: string
  quantity: number
  reason_code: string
  reason_text: string
  blocked_by: string | null
  current_price: number
  target_amount: number
  industry_position_pct: number
  total_position_pct: number
  explain?: Record<string, unknown>
  priority: number
}

export interface StrategyRun {
  id: number
  strategy_id: string
  run_date: string
  run_type: string
  signal_count: number
  decision_count: number
  status: string
  error_msg: string | null
  created_at: string
}

export const strategyService = {
  listStrategies() {
    return api.get('/api/strategy/list')
  },

  getConfig(strategyId: string) {
    return api.get(`/api/strategy/${strategyId}/config`)
  },

  updateConfig(strategyId: string, configYaml: string) {
    return api.put(`/api/strategy/${strategyId}/config`, {
      config_yaml: configYaml
    })
  },

  runStrategy(strategyId: string, accountId?: string) {
    const params = accountId ? `?account_id=${accountId}` : ''
    return api.post(`/api/strategy/${strategyId}/run${params}`)
  },

  getSignals(strategyId: string, accountId?: string, runId?: number, signal?: string) {
    const params = new URLSearchParams()
    if (accountId) params.set('account_id', accountId)
    if (runId) params.set('run_id', String(runId))
    if (signal) params.set('signal', signal)
    const qs = params.toString() ? `?${params.toString()}` : ''
    return api.get(`/api/strategy/${strategyId}/signals${qs}`)
  },

  getDecisions(strategyId: string, accountId?: string, runId?: number) {
    const params = new URLSearchParams()
    if (accountId) params.set('account_id', accountId)
    if (runId) params.set('run_id', String(runId))
    const qs = params.toString() ? `?${params.toString()}` : ''
    return api.get(`/api/strategy/${strategyId}/decisions${qs}`)
  },

  getRuns(strategyId: string, accountId?: string, limit = 20) {
    const params = new URLSearchParams()
    if (accountId) params.set('account_id', accountId)
    params.set('limit', String(limit))
    return api.get(`/api/strategy/${strategyId}/runs?${params.toString()}`)
  }
}
