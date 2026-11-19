import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { fetchAIJSON } from '../hooks/useApi'
import { invalidateSession } from '../hooks/sessionRuntime'

beforeEach(() => {
  vi.stubGlobal('window', new EventTarget())
  vi.stubGlobal('CustomEvent', class extends Event {})
  invalidateSession()
})
afterEach(() => vi.unstubAllGlobals())

it('离开后切回继续等待原请求，强制刷新也不重复排队', async () => {
  let finish
  vi.stubGlobal('fetch', vi.fn(() => new Promise(resolve => { finish = resolve })))
  const first = fetchAIJSON('/api/market/sentiment-ai')
  const resumed = fetchAIJSON('/api/market/sentiment-ai?force=true')
  expect(fetch).toHaveBeenCalledTimes(1)
  finish(new Response('{"summary":"结果"}'))
  expect(await first).toEqual({ summary: '结果' })
  expect(await resumed).toEqual({ summary: '结果' })
})

it.each(['/api/market/sentiment-ai', '/api/market/structure', '/api/sector/trend-ai?days=10', '/api/news/digest', '/api/news/daily-review', '/api/portfolio/trade-review-ai?period=day'])('完成后切回 %s 直接复用结果', async path => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{"summary":"结果"}')))
  await fetchAIJSON(path)
  expect(await fetchAIJSON(path)).toEqual({ summary: '结果' })
  expect(fetch).toHaveBeenCalledTimes(1)
})

it('失败后切页不反复自动重试，手动刷新可重试', async () => {
  vi.stubGlobal('fetch', vi.fn().mockRejectedValueOnce(new Error('队列已满')))
  await expect(fetchAIJSON('/api/news/digest')).rejects.toThrow('队列已满')
  await expect(fetchAIJSON('/api/news/digest')).rejects.toThrow('队列已满')
  expect(fetch).toHaveBeenCalledTimes(1)
  fetch.mockResolvedValueOnce(new Response('{"summary":"重试结果"}'))
  expect(await fetchAIJSON('/api/news/digest?force=true')).toEqual({ summary: '重试结果' })
  expect(fetch).toHaveBeenCalledTimes(2)
})

it('HTTP 成功但业务返回队列满时也复用失败结果，避免切页自动重试', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{"error":"本地队列已满"}')))
  expect(await fetchAIJSON('/api/news/digest')).toEqual({ error: '本地队列已满' })
  expect(await fetchAIJSON('/api/news/digest')).toEqual({ error: '本地队列已满' })
  expect(fetch).toHaveBeenCalledTimes(1)
})

it('区分分析参数、北京时间自然日和账号边界', async () => {
  let now = 0
  vi.spyOn(Date, 'now').mockImplementation(() => now)
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response('{"summary":"结果"}'))))
  try {
    await fetchAIJSON('/api/sector/trend-ai?days=10')
    await fetchAIJSON('/api/sector/trend-ai?days=20')
    expect(fetch).toHaveBeenCalledTimes(2)
    now = 31 * 60_000
    await fetchAIJSON('/api/sector/trend-ai?days=10')
    expect(fetch).toHaveBeenCalledTimes(2)
    now = 16 * 60 * 60_000 // UTC 16:00 是北京时间次日零点。
    await fetchAIJSON('/api/sector/trend-ai?days=10')
    expect(fetch).toHaveBeenCalledTimes(3)
    invalidateSession()
    await fetchAIJSON('/api/sector/trend-ai?days=10')
    expect(fetch).toHaveBeenCalledTimes(4)
  } finally { vi.restoreAllMocks() }
})

it('自动失败整天不重试，跨日或手动重新生成才再次请求', async () => {
  let now = 0
  vi.spyOn(Date, 'now').mockImplementation(() => now)
  vi.stubGlobal('fetch', vi.fn().mockRejectedValueOnce(new Error('合成失败')))
  try {
    await expect(fetchAIJSON('/api/news/digest')).rejects.toThrow('合成失败')
    now = 2 * 60 * 60_000
    await expect(fetchAIJSON('/api/news/digest')).rejects.toThrow('合成失败')
    expect(fetch).toHaveBeenCalledTimes(1)
    fetch.mockImplementation(() => Promise.resolve(new Response('{"summary":"新结果"}')))
    await fetchAIJSON('/api/news/digest?force=true')
    expect(fetch).toHaveBeenCalledTimes(2)
    now = 16 * 60 * 60_000
    await fetchAIJSON('/api/news/digest')
    expect(fetch).toHaveBeenCalledTimes(3)
  } finally { vi.restoreAllMocks() }
})

it.each(['http', 'body'])('手动重新生成 %s 失败后，切回仍显示当天成功结果', async failure => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(new Response('{"summary":"已有结果"}')))
  await fetchAIJSON('/api/news/digest')
  if (failure === 'http') {
    fetch.mockRejectedValueOnce(new Error('合成失败'))
    await expect(fetchAIJSON('/api/news/digest?force=true')).rejects.toThrow('合成失败')
  } else {
    fetch.mockResolvedValueOnce(new Response('{"error":"合成失败"}'))
    expect(await fetchAIJSON('/api/news/digest?force=true')).toEqual({ error: '合成失败' })
  }
  expect(await fetchAIJSON('/api/news/digest')).toEqual({ summary: '已有结果' })
  expect(fetch).toHaveBeenCalledTimes(2)
})
