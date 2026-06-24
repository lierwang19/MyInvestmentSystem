<script lang="ts">
/**
 * ErrorBoundary — catches unhandled errors in child components
 * and displays a friendly recovery UI instead of a white screen.
 * 
 * Usage: wrap any component tree in <ErrorBoundary>
 *   <ErrorBoundary><RouterView /></ErrorBoundary>
 */
import { defineComponent, ref, h, onErrorCaptured } from 'vue'
import { ElResult, ElButton } from 'element-plus'

export default defineComponent({
    name: 'ErrorBoundary',
    setup(_, { slots }) {
        const hasError = ref(false)
        const errorMessage = ref('')

        onErrorCaptured((err: Error) => {
            hasError.value = true
            errorMessage.value = err.message || '未知错误'
            console.error('[ErrorBoundary]', err)
            return false // prevent propagation
        })

        const reset = () => {
            hasError.value = false
            errorMessage.value = ''
        }

        return () => {
            if (hasError.value) {
                return h('div', { style: 'display: flex; justify-content: center; align-items: center; min-height: 60vh;' }, [
                    h(ElResult, {
                        icon: 'warning',
                        title: '页面加载异常',
                        subTitle: errorMessage.value
                    }, {
                        extra: () => h(ElButton, { type: 'primary', onClick: reset }, () => '重新加载')
                    })
                ])
            }
            return slots.default?.()
        }
    }
})
</script>
