import { beforeEach, describe, expect, it, vi } from 'vitest'

let requestInterceptor: ((config: any) => any) | undefined
let responseErrorInterceptor: ((error: any) => Promise<never>) | undefined
const mockRequest = vi.fn()
const mockMessageInfo = vi.fn()

vi.mock('axios', () => {
    const create = vi.fn(() => ({
        interceptors: {
            request: {
                use: (fn: (config: any) => any) => {
                    requestInterceptor = fn
                },
            },
            response: {
                use: (_ok: unknown, err: (error: any) => Promise<never>) => {
                    responseErrorInterceptor = err
                },
            },
        },
        request: mockRequest,
    }))

    return {
        default: { create },
        create,
    }
})

vi.mock('element-plus', () => ({
    ElMessage: {
        info: mockMessageInfo,
        warning: vi.fn(),
        error: vi.fn(),
    },
}))

describe('admin token interceptor', () => {
    beforeEach(async () => {
        vi.resetModules()
        window.localStorage.clear()
        window.sessionStorage.clear()
        mockRequest.mockReset()
        mockMessageInfo.mockReset()
        requestInterceptor = undefined
        responseErrorInterceptor = undefined
        await import('./api')
    })

    it('attaches admin token from localStorage', () => {
        window.localStorage.setItem('invest-admin-token', 'persisted-token')

        const config = requestInterceptor?.({ headers: {} })
        expect(config.headers['X-Admin-Token']).toBe('persisted-token')
    })

    it('cancelling token prompt aborts the write request explicitly', async () => {
        vi.spyOn(window, 'prompt').mockReturnValue(null)

        await expect(responseErrorInterceptor?.({
            response: { status: 401, data: { detail: '需要管理员令牌' } },
            config: { headers: {} },
        })).rejects.toMatchObject({ code: 'ADMIN_TOKEN_INPUT_CANCELLED' })

        expect(mockRequest).not.toHaveBeenCalled()
        expect(mockMessageInfo).toHaveBeenCalledWith('已取消管理员令牌输入，本次写操作未执行')
    })

    it('migrates legacy session token into localStorage and retries once', async () => {
        window.sessionStorage.setItem('invest-admin-token', 'legacy-token')

        await responseErrorInterceptor?.({
            response: { status: 401, data: { detail: '需要管理员令牌' } },
            config: { headers: {} },
        })

        expect(window.localStorage.getItem('invest-admin-token')).toBe('legacy-token')
        expect(window.sessionStorage.getItem('invest-admin-token')).toBeNull()
        expect(mockRequest).toHaveBeenCalledWith(expect.objectContaining({
            _retryAdminToken: true,
            headers: expect.objectContaining({
                'X-Admin-Token': 'legacy-token',
            }),
        }))
    })
})
