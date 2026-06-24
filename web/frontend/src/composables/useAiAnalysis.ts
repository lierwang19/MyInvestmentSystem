import { ref } from 'vue'

/**
 * AI 风险分析 Composable
 * 通过 SSE 流式调用本地 Ollama，逐 token 输出分析结果。
 * 使用 fetch() + ReadableStream 手动解析 SSE（而非 EventSource，
 * 因为 EventSource 不支持自定义请求头且错误处理有限）。
 */
export function useAiAnalysis() {
  const aiAnalyzing = ref(false)
  const aiResult = ref('')
  const aiError = ref('')
  let abortController: AbortController | null = null

  const runAiAnalysis = async (tsCode: string, name: string) => {
    // 中止上一次未完成的请求
    abortController?.abort()
    abortController = new AbortController()

    aiAnalyzing.value = true
    aiResult.value = ''
    aiError.value = ''

    try {
      const url = `/api/ai/analyze?ts_code=${encodeURIComponent(tsCode)}&name=${encodeURIComponent(name)}`
      const resp = await fetch(url, { signal: abortController.signal })

      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: resp.statusText }))
        throw new Error(err.detail || `请求失败 (${resp.status})`)
      }

      const reader = resp.body!.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        // 按换行拆分，处理完整的 SSE 行
        const lines = buffer.split('\n')
        buffer = lines.pop() ?? '' // 最后一行可能不完整，留到下次

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            aiResult.value += line.slice(6)
          } else if (line.startsWith('event: error')) {
            // 下一个 data: 行是错误消息，由下次循环捕获并追加到 aiResult
            // 此处不单独处理，让用户看到错误内容
          }
        }
      }
    } catch (e: any) {
      if (e.name !== 'AbortError') {
        aiError.value = e.message || 'AI 分析请求失败'
      }
    } finally {
      aiAnalyzing.value = false
    }
  }

  const stopAnalysis = () => {
    abortController?.abort()
    aiAnalyzing.value = false
  }

  return { aiAnalyzing, aiResult, aiError, runAiAnalysis, stopAnalysis }
}
