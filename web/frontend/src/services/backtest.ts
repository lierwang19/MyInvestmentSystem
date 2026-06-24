import api from '../api'

export interface BacktestRunRequest {
  strategy_id: string
  name?: string
  start_date: string
  end_date: string
  init_cash: number
}

export interface BacktestRecord {
  id: number
  strategy_id: string
  name: string
  start_date: string
  end_date: string
  init_cash: number
  status: string
  result_json: string | null
  result?: {
    final_nav: number
    return_pct: number
    max_drawdown_pct: number
    nav_count: number
    trade_count: number
    error?: string
  }
  created_at: string
}

export interface NavRecord {
  date: string
  total_nav: number
  cash: number
  market_value: number
  position_pct: number
  holding_count: number
}

export interface TradeRecord {
  id: number
  backtest_id: number
  date: string
  ts_code: string
  name: string
  action: string
  quantity: number
  price: number
  amount: number
  commission: number
  stamp_tax: number
  reason_text: string
}

export const backtestService = {
  runBacktest(body: BacktestRunRequest) {
    return api.post('/api/backtest/run', body)
  },

  listBacktests() {
    return api.get('/api/backtest/list')
  },

  getBacktest(id: number) {
    return api.get(`/api/backtest/${id}`)
  },

  getNav(id: number) {
    return api.get(`/api/backtest/${id}/nav`)
  },

  getTrades(id: number) {
    return api.get(`/api/backtest/${id}/trades`)
  },

  deleteBacktest(id: number) {
    return api.delete(`/api/backtest/${id}`)
  },
}
