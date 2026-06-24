/**
 * Real-time quotes API service
 */
import api from '../api'

export const quotesService = {
    getRealtime: () => api.get('/api/quotes/realtime'),
    getQuotes: (codes?: string[]) => {
        const params = codes ? { codes: codes.join(',') } : {}
        return api.get('/api/quotes/realtime', { params })
    },
    getWatchlist: () => api.get('/api/quotes/watchlist'),
    refreshWatchlist: () => api.post('/api/quotes/refresh-watchlist'),
    fetchNow: () => api.post('/api/quotes/fetch-now'),
    getStatus: () => api.get('/api/quotes/status'),
    startPoller: () => api.post('/api/quotes/start'),
    stopPoller: () => api.post('/api/quotes/stop'),
}
