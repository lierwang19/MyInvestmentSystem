/**
 * Settings API service
 */
import api from '../api'

export const settingsService = {
    getAll: () => api.get('/api/settings'),
    get: (key: string) => api.get(`/api/settings/${key}`),
    update: (key: string, value: string) => api.put(`/api/settings/${key}`, { value }),
    updateAll: (settings: Record<string, string>) => api.put('/api/settings', { settings }),
}
