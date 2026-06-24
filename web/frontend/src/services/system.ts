/**
 * System management API service
 */
import api from '../api'

export const systemService = {
    // Market data refresh
    refreshMarketData: () => api.post('/api/system/refresh-market'),

    // ETL market
    triggerEtl: () => api.post('/api/system/etl-market'),

    // System status
    getStatus: () => api.get('/api/system/status'),

    // Scheduler config
    getSchedulerConfig: () => api.get('/api/system/scheduler-config'),
    updateSchedulerConfig: (data: Record<string, unknown>) => api.put('/api/system/scheduler-config', data),

    // Backup & Restore
    backup: () => api.get('/api/system/backup', { responseType: 'blob' }),
    restore: (file: File) => {
        const form = new FormData()
        form.append('file', file)
        return api.post('/api/system/restore', form)
    },

    // Data health
    getDataHealth: () => api.get('/api/system/data-health'),

    // Logs
    getLog: (filename: string) => api.get(`/api/system/logs/${filename}`),

    // Industry sync
    syncIndustry: () => api.post('/api/system/sync-industry'),
    getSyncIndustryStatus: () => api.get('/api/system/sync-industry/status'),
    getInconStatus: () => api.get('/api/system/incon-status'),

    // Industry mapping upload
    uploadIndustryMapping: (file: File) => {
        const form = new FormData()
        form.append('file', file)
        return api.post('/api/system/upload-industry-mapping', form)
    },
}
