/**
 * Accounts API service
 */
import api from '../api'

export const accountsService = {
    getAll: () => api.get('/api/accounts-db'),
    create: (name: string, brokerId?: number) => api.post('/api/accounts-db', { name, broker_id: brokerId }),
    update: (id: string, data: Record<string, unknown>) => api.put(`/api/accounts-db/${id}`, data),
    delete: (id: string) => api.delete(`/api/accounts-db/${id}`),
    getCapitalFlows: (accountId: string) => api.get(`/api/accounts-db/${accountId}/capital-flows`),
    addCapitalFlow: (accountId: string, data: Record<string, unknown>) => api.post(`/api/accounts-db/${accountId}/capital-flows`, data),
    deleteCapitalFlow: (flowId: number) => api.delete(`/api/capital-flows/${flowId}`),
}
