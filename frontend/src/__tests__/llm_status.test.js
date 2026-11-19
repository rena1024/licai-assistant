import { describe, it, expect, vi } from 'vitest'
import { modelActivityLabel, llmRequestHeaders } from '../hooks/useLLMStatus'
import { invalidateSession } from '../hooks/sessionRuntime'

describe('模型调用提示', () => {
  it('切回同一分析复用状态标识，不同参数分开，切换账号后重建', () => {
    const first = llmRequestHeaders('sector:10')
    expect(llmRequestHeaders('sector:10')).toBe(first)
    expect(llmRequestHeaders('sector:20')['X-LLM-Activity']).not.toBe(first['X-LLM-Activity'])
    vi.stubGlobal('window', new EventTarget())
    vi.stubGlobal('CustomEvent', class extends Event {})
    try {
      invalidateSession()
      expect(llmRequestHeaders('sector:10')['X-LLM-Activity']).not.toBe(first['X-LLM-Activity'])
    } finally { vi.unstubAllGlobals() }
  })
  it('尚未调用时不猜测模型', () => {
    expect(modelActivityLabel([])).toBe('正在准备 AI 请求…')
  })
  it('显示实际模型，并区分排队、本地和云端', () => {
    const call = { model: 'qwen3:8b', model_id: 'local_ollama', local: true, status: 'queued' }
    expect(modelActivityLabel([call])).toContain('正在等待 qwen3:8b（local_ollama）（本地排队）')
    expect(modelActivityLabel([{ ...call, status: 'running' }])).toContain('正在调用 qwen3:8b（local_ollama）（本地）')
    expect(modelActivityLabel([{ model: 'deepseek-flash', model_id: 'deepseek_flash', local: false, status: 'running' }])).toContain('正在调用 deepseek-flash（deepseek_flash）（云端）')
  })
  it('显示后端报告的等待时长，长时间无正文时明确仍在等待响应', () => {
    const label = modelActivityLabel([{ model: 'deepseek-flash', model_id: 'deepseek_flash', local: false, status: 'running', elapsed_seconds: 65 }])
    expect(label).toContain('已等待 65 秒')
    expect(label).toContain('尚未收到完整响应')
  })
})
