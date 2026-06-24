/**
 * Stock pool and risk flags API service
 */
import api from '../api'

export const stockPoolService = {
    scan: () => api.post('/api/analysis/stock-pool/scan'),
    getPool: () => api.get('/api/analysis/stock-pool'),
    getRiskFlags: (params?: { industry?: string; risk_level?: string }) => api.get('/api/analysis/risk-flags', { params }),
    // source: "offline" | "akshare" | "tushare"；pool_only=true 只扫优选池
    scanRiskFlags: (source = 'offline', poolOnly = false) =>
        api.post(`/api/analysis/risk-flags/scan?source=${source}&pool_only=${poolOnly}`, null, { timeout: 300000 }),
}
