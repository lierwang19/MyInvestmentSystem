/**
 * Asset history API service
 */
import api from '../api'

export const assetsService = {
    takeSnapshot: () => api.post('/api/assets/snapshot'),
    getHistory: (params?: { account_id?: string; days?: number }) => api.get('/api/assets/history', { params }),
}
