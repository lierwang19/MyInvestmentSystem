/**
 * Holdings analysis API service
 */
import api from '../api'

export const holdingsService = {
    analyzeHoldings: (accountId: string) => api.get(`/api/analysis/holdings/${accountId}`),
    getMarketOverview: () => api.get('/api/analysis/market-overview'),
    analyzeDividends: (accountId: string) => api.get(`/api/analysis/dividends/${accountId}`),
}
