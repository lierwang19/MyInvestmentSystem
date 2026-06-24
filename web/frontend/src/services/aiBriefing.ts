import api from '../api'

export const aiBriefingService = {
  getBriefings: (date?: string) => api.get('/api/ai/briefings', { params: date ? { date } : {} }),
  getProgress: () => api.get('/api/ai/briefings/progress'),
  triggerRun: () => api.post('/api/ai/briefings/run'),
  getDates: () => api.get('/api/ai/briefings/dates'),
}
