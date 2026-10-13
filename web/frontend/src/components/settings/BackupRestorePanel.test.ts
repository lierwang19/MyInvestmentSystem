import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent } from 'vue'
import { mount } from '@vue/test-utils'
import BackupRestorePanel from './BackupRestorePanel.vue'

const mocks = vi.hoisted(() => ({ post: vi.fn(), confirm: vi.fn(), close: vi.fn() }))
vi.mock('../../api', () => ({ default: { post: mocks.post } }))
vi.mock('element-plus', () => ({
    ElMessage: Object.assign(vi.fn(() => ({ close: mocks.close })), {
        success: vi.fn(), error: vi.fn(),
    }),
    ElMessageBox: { confirm: mocks.confirm },
}))

const Upload = defineComponent({
    name: 'ElUpload', props: { onChange: Function }, template: '<div><slot /></div>',
})

function mountPanel() {
    return mount(BackupRestorePanel, {
        global: { stubs: { ElUpload: Upload, ElButton: { template: '<button><slot /></button>' }, ElIcon: true } },
    })
}

describe('restore user-facing contract', () => {
    beforeEach(() => {
        vi.clearAllMocks()
        vi.useFakeTimers()
        mocks.confirm.mockResolvedValue('confirm')
        vi.spyOn(console, 'error').mockImplementation(() => {})
    })
    afterEach(() => {
        vi.clearAllTimers()
        vi.useRealTimers()
        vi.restoreAllMocks()
    })

    it('keeps a visible pending-restart state without reloading after staging', async () => {
        mocks.post.mockResolvedValue({ data: { status: 'success', message: '数据库仅暂存，重启后端后才会生效' } })
        const wrapper = mountPanel()
        const change = wrapper.getComponent(Upload).props('onChange') as (file: { raw: File }) => Promise<void>
        try {
            await change({ raw: new File(['backup'], 'backup.zip') })
            expect(vi.getTimerCount(), '刷新页面不能应用暂存数据库').toBe(0)
            expect(wrapper.text()).toMatch(/待重启|重启后端/)
        } finally {
            wrapper.unmount()
        }
    })

    it('closes the indefinite loading message when upload fails', async () => {
        mocks.post.mockRejectedValue(new Error('network failed'))
        const wrapper = mountPanel()
        const change = wrapper.getComponent(Upload).props('onChange') as (file: { raw: File }) => Promise<void>
        try {
            await change({ raw: new File(['backup'], 'backup.zip') })
            expect(mocks.close).toHaveBeenCalledOnce()
        } finally {
            wrapper.unmount()
        }
    })
})
