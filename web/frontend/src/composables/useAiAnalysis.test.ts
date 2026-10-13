import { afterEach, describe, expect, it, vi } from 'vitest'
import { useAiAnalysis } from './useAiAnalysis'

function mockStream(chunks: string[]) {
  const encoder = new TextEncoder()
  let i = 0
  const reader = {
    read: async () => (i < chunks.length
      ? { done: false, value: encoder.encode(chunks[i++]) }
      : { done: true, value: undefined }),
  }
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, body: { getReader: () => reader } })))
}

describe('useAiAnalysis', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('joins multi-line data within one SSE event with newlines', async () => {
    // 后端把含换行的 token 拆成多行 data:；chunk 边界故意切在行中间
    mockStream(['data: 1. 估值\n\ndata: ：低估\ndata: 2. 风', '险：无\n\nevent: done\ndata: \n\n'])
    const { aiResult, aiError, aiAnalyzing, runAiAnalysis } = useAiAnalysis()
    await runAiAnalysis('000001.SZ', '平安银行')
    expect(aiResult.value).toBe('1. 估值：低估\n2. 风险：无')
    expect(aiError.value).toBe('')
    expect(aiAnalyzing.value).toBe(false)
  })

  it('shows error event content', async () => {
    mockStream(['event: error\ndata: LLM调用异常: refused\n\n'])
    const { aiResult, runAiAnalysis } = useAiAnalysis()
    await runAiAnalysis('000001.SZ', '')
    expect(aiResult.value).toBe('LLM调用异常: refused')
  })
})
