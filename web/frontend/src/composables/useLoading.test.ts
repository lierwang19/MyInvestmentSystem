/**
 * Unit tests for useLoading composable.
 */
import { describe, it, expect, vi } from 'vitest'
import { useLoading } from '../composables/useLoading'

// Mock element-plus
vi.mock('element-plus', () => ({
    ElMessage: {
        error: vi.fn(),
        success: vi.fn(),
        warning: vi.fn(),
    },
}))

describe('useLoading', () => {
    it('should initialize with default state', () => {
        const { loading, error } = useLoading()
        expect(loading.value).toBe(false)
        expect(error.value).toBeNull()
    })

    it('should set loading=true during async operation', async () => {
        const { loading, run } = useLoading()
        let resolvePromise: (() => void) | null = null

        const promise = run(() => new Promise<void>((resolve) => {
            resolvePromise = resolve
        }))

        // Loading should be true while promise is pending
        expect(loading.value).toBe(true)

        // Resolve the promise
        resolvePromise!()
        await promise

        // Loading should be false after completion
        expect(loading.value).toBe(false)
    })

    it('should return result on success', async () => {
        const { run } = useLoading()
        const result = await run(() => Promise.resolve(42))
        expect(result).toBe(42)
    })

    it('should return null and set error on failure', async () => {
        const { error, run } = useLoading()
        const result = await run(
            () => Promise.reject(new Error('test error')),
            { showError: false }
        )
        expect(result).toBeNull()
        expect(error.value).toBe('test error')
    })

    it('should use custom error message when provided', async () => {
        const { error, run } = useLoading()
        await run(
            () => Promise.reject(new Error('internal')),
            { showError: false, errorMessage: 'custom message' }
        )
        expect(error.value).toBe('custom message')
    })

    it('should reset error on new run', async () => {
        const { error, run } = useLoading()

        // First call: fail
        await run(() => Promise.reject(new Error('fail')), { showError: false })
        expect(error.value).toBe('fail')

        // Second call: succeed
        await run(() => Promise.resolve('ok'), { showError: false })
        expect(error.value).toBeNull()
    })
})
