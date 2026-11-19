import { useEffect, useState } from 'react'
import { fetchJSON } from './useApi'
import { onSessionReset } from './sessionRuntime'

const requestHeaders = new Map()
onSessionReset(() => requestHeaders.clear())

export function modelActivityLabel(calls) {
  if (!calls?.length) return '正在准备 AI 请求…'
  return [...new Set(calls.map(call => {
    const name = `${call.model}${call.model_id !== call.model ? `（${call.model_id}）` : ''}`
    const elapsed = Number.isFinite(call.elapsed_seconds) ? ` · 已等待 ${call.elapsed_seconds} 秒` : ''
    if (call.status === 'queued') return `正在等待 ${name}（本地排队）${elapsed}…`
    if (call.status === 'preparing') return `正在准备调用 ${name}${elapsed}…`
    const waiting = call.elapsed_seconds >= 30 ? ' · 尚未收到完整响应，可继续等待或停止' : ''
    return `正在调用 ${name}（${call.local ? '本地' : '云端'}）${elapsed}${waiting}…`
  }))].join('；')
}

// 请求标识只关联执行元信息；模型名来自后端实际执行，不能用偏好中的主模型推测。
export function newLLMRequestId() {
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, '0')).join('')
}

export function llmRequestHeaders(key = '') {
  if (key && requestHeaders.has(key)) return requestHeaders.get(key)
  const headers = { 'Content-Type': 'application/json', 'X-LLM-Activity': newLLMRequestId() }
  if (key) requestHeaders.set(key, headers)
  return headers
}

export default function useLLMStatus(active, { taskId = '', requestId: trackedId = '', requestKey = '' } = {}) {
  const [ownHeaders] = useState(() => llmRequestHeaders())
  const headers = requestKey ? llmRequestHeaders(requestKey) : ownHeaders
  const requestId = trackedId || headers['X-LLM-Activity']
  const query = taskId ? `task_id=${encodeURIComponent(taskId)}` : `request_id=${requestId}`
  const [progress, setProgress] = useState({ active, query, calls: [] })
  if (progress.active !== active || progress.query !== query) {
    setProgress({ active, query, calls: [] })
  }
  useEffect(() => {
    if (!active) return
    let current = true, timer
    const controller = new AbortController()
    const poll = async () => {
      try {
        const data = await fetchJSON(`/api/models/activity?${query}`, { signal: controller.signal })
        if (current) setProgress({ active, query, calls: data.calls || [] })
      } catch { if (current) setProgress({ active, query, calls: [], error: true }) }
      if (current) timer = setTimeout(poll, 800)
    }
    poll()
    return () => { current = false; clearTimeout(timer); controller.abort() }
  }, [active, query])
  return { headers, label: progress.error ? 'AI 请求处理中，模型状态暂不可读…' : modelActivityLabel(progress.calls) }
}
