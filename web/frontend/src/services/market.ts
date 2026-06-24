/**
 * Market data API service
 */
import api from '../api'

export const marketService = {
    getCurrentStatus: () => api.get('/api/market/current'),
    getMonthlyHistory: (years = 5) => api.get(`/api/market/monthly?years=${years}`),
    syncMarketData: (startDate?: string) => api.post('/api/market/sync', null, { params: { start_date: startDate } }),
    getAccountsSummary: () => api.get('/api/market/accounts-summary'),
    getTargetPosition: (accountId: string) => api.get(`/api/market/target-position?account_id=${accountId}`),
    searchStocks: (q: string) => api.get(`/api/market/search?q=${q}`),
    getStockInfo: (tsCode: string) => api.get(`/api/stocks/info/${tsCode}`),
    getStockValuation: (tsCode: string) => api.get(`/api/stocks/valuation/${tsCode}`),
    getStockKline: (tsCode: string, days = 252) => api.get(`/api/stocks/kline/${tsCode}?days=${days}`),
}
