import api from '../api'

export const ipoService = {
  getCalendar: (days = 7) => api.get('/api/ipo/calendar', { params: { days } }),
  getToday: () => api.get('/api/ipo/today'),
  syncCalendar: (days = 7) => api.post('/api/ipo/sync', { days }),
}
