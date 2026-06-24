/**
 * Composable for unified loading state management.
 *
 * Usage:
 *   const { loading, run } = useLoading()
 *   const data = await run(() => api.get('/endpoint'))
 */
import { ref } from 'vue'
import { ElMessage } from 'element-plus'

export function useLoading() {
    const loading = ref(false)
    const error = ref<string | null>(null)

    /**
     * Wrap an async operation with automatic loading/error state management.
     * @param fn  Async function to execute
     * @param opts  Options: showError (show ElMessage on failure), errorMessage (custom message)
     */
    async function run<T>(
        fn: () => Promise<T>,
        opts: { showError?: boolean; errorMessage?: string } = {}
    ): Promise<T | null> {
        const { showError = true, errorMessage } = opts
        loading.value = true
        error.value = null
        try {
            const result = await fn()
            return result
        } catch (e: any) {
            const msg = errorMessage || e?.response?.data?.message || e?.message || '操作失败'
            error.value = msg
            if (showError) {
                ElMessage.error(msg)
            }
            return null
        } finally {
            loading.value = false
        }
    }

    return { loading, error, run }
}
