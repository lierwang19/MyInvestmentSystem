/**
 * Axios global configuration with base URL and error handling interceptors.
 * Import this configured instance instead of raw axios in all components.
 */
import axios from 'axios'
import { ElMessage } from 'element-plus'

const apiBase = import.meta.env.VITE_API_BASE || ''
const ADMIN_TOKEN_STORAGE_KEY = 'invest-admin-token'
const ADMIN_TOKEN_CANCELLED = 'ADMIN_TOKEN_INPUT_CANCELLED'
const adminTokenStorage = window.localStorage
const legacyAdminTokenStorage = window.sessionStorage

const api = axios.create({
    baseURL: apiBase,
    timeout: 30000, // 30s timeout
})

api.interceptors.request.use((config) => {
    const token = adminTokenStorage.getItem(ADMIN_TOKEN_STORAGE_KEY)
    if (token) {
        config.headers = config.headers || {}
        config.headers['X-Admin-Token'] = token
    }
    return config
})

// ── Response Interceptor ──
api.interceptors.response.use(
    (response) => response,
    async (error) => {
        if (error.response?.status === 401 && error.config && !error.config._retryAdminToken) {
            const legacyToken = legacyAdminTokenStorage.getItem(ADMIN_TOKEN_STORAGE_KEY)
            if (legacyToken) {
                adminTokenStorage.setItem(ADMIN_TOKEN_STORAGE_KEY, legacyToken)
                legacyAdminTokenStorage.removeItem(ADMIN_TOKEN_STORAGE_KEY)
                error.config._retryAdminToken = true
                error.config.headers = error.config.headers || {}
                error.config.headers['X-Admin-Token'] = legacyToken
                return api.request(error.config)
            }
            const entered = window.prompt('请输入管理员令牌')
            const trimmed = entered?.trim()
            if (trimmed) {
                adminTokenStorage.setItem(ADMIN_TOKEN_STORAGE_KEY, trimmed)
                error.config._retryAdminToken = true
                error.config.headers = error.config.headers || {}
                error.config.headers['X-Admin-Token'] = trimmed
                return api.request(error.config)
            }
            ElMessage.info('已取消管理员令牌输入，本次写操作未执行')
            return Promise.reject(Object.assign(new Error('管理员令牌输入已取消'), {
                code: ADMIN_TOKEN_CANCELLED,
                response: error.response,
            }))
        }
        if (error.code === ADMIN_TOKEN_CANCELLED) {
            return Promise.reject(error)
        }
        if (error.response) {
            const status = error.response.status
            const detail = error.response.data?.detail || error.message

            if (status === 500) {
                // Hide raw DB/schema details from users — log to console for debugging
                console.error('[API 500]', detail)
                ElMessage.error('服务器出错，请稍后重试')
            } else if (status === 404) {
                ElMessage.warning(`未找到: ${detail}`)
            } else if (status === 401) {
                ElMessage.warning('缺少或无效的管理员令牌')
            } else if (status >= 400) {
                ElMessage.warning(detail)
            }
        } else if (error.code === 'ECONNABORTED') {
            ElMessage.error('请求超时，请检查网络连接')
        } else {
            ElMessage.error('网络连接失败')
        }
        return Promise.reject(error)
    }
)

/**
 * Returns a user-friendly error message from a caught axios error.
 * 5xx (server error): generic message — never expose raw DB/schema details.
 * 4xx (client error): return detail — it's usually meaningful validation feedback.
 * Network error: generic message.
 */
export function userMessage(error: unknown, fallback = '操作失败'): string {
    const e = error as any
    if (e?.response?.status >= 500) return '服务器出错，请稍后重试'
    return e?.response?.data?.detail || e?.message || fallback
}

export default api
