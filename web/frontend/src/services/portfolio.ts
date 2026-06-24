/**
 * Portfolio generation API service
 */
import api from '../api'

export const portfolioService = {
    generate: (params: { account_id: string; total_budget?: number }) => api.post('/api/portfolio/generate', params),
}
