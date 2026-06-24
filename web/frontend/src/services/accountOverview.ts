import api from '../api'

export const accountOverviewService = {
  getOverview: () => api.get('/api/analysis/account-overview'),
}
