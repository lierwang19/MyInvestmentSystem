/**
 * T-Trading (做T分析) API service
 * Supports BUY (买入分析) and SELL (卖出分析) dual-mode
 */
import api from '../api'

export const tTradingService = {
    getItems: (accountId: string, side: string = 'BUY') =>
        api.get(`/api/t-trading/${accountId}`, { params: { side } }),

    importDays: (accountId: string, days: number, side: string = 'BUY') =>
        api.post(`/api/t-trading/${accountId}/import`, { days, side }),

    importOne: (accountId: string, transaction_id: number, side: string = 'BUY') =>
        api.post(`/api/t-trading/${accountId}/import-one`, { transaction_id, side }),

    deleteItem: (itemId: number) =>
        api.delete(`/api/t-trading/${itemId}`),

    clearAll: (accountId: string, side?: string) =>
        api.delete(`/api/t-trading/${accountId}/clear`, { params: side ? { side } : {} }),
}
