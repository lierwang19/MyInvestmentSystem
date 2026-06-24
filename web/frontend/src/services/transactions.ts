/**
 * Transactions API service
 */
import api from '../api'

export interface TransactionPayload {
    ts_code: string
    name: string
    trans_type: 'BUY' | 'SELL' | 'DIVIDEND' | 'SPLIT' | 'RIGHTS'
    price: number
    amount: number
    date: string
    fee?: number
    stamp_tax?: number
    remarks?: string
}

export const transactionsService = {
    getByAccount: (accountId: string, tsCode?: string) => {
        const params = tsCode ? { ts_code: tsCode } : {}
        return api.get(`/api/transactions/${accountId}`, { params })
    },
    create: (accountId: string, data: TransactionPayload) =>
        api.post(`/api/transactions/${accountId}`, data),
    deleteById: (id: number) =>
        api.delete(`/api/transactions/${id}`),
    getStockSummary: (accountId: string, tsCode: string) =>
        api.get(`/api/stock-summary/${accountId}/${tsCode}`),
    updateHoldingCost: (accountId: string, tsCode: string, costPrice: number, totalCost: number) =>
        api.put(`/api/holdings/${accountId}/${tsCode}/cost`, { cost_price: costPrice, total_cost: totalCost }),

    // Archive endpoints
    getArchivedHoldings: (accountId: string) =>
        api.get(`/api/archived/${accountId}`),
    archiveHolding: (accountId: string, tsCode: string, stockName?: string) =>
        api.post(`/api/archived/${accountId}`, { ts_code: tsCode, stock_name: stockName }),
    addManualArchive: (accountId: string, data: Record<string, unknown>) =>
        api.post(`/api/archived/${accountId}/manual`, data),
    updateArchived: (accountId: string, id: number, data: Record<string, unknown>) =>
        api.put(`/api/archived/${accountId}/${id}`, data),
}
