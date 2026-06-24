import api from '../api'

export const watchlistService = {
  getItems:      ()                                    => api.get('/api/watchlist'),
  addItem:       (ts_code: string, name: string, note?: string) =>
                   api.post('/api/watchlist', { ts_code, name, note: note || '' }),
  updateItem:    (itemId: number, data: { note?: string; sort_order?: number }) =>
                   api.put(`/api/watchlist/${itemId}`, data),
  deleteItem:    (itemId: number)                      => api.delete(`/api/watchlist/${itemId}`),
  clearRemoved:  ()                                    => api.delete('/api/watchlist/clear-removed'),
}
