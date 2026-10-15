import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

beforeEach(() => {
  vi.stubGlobal('window', new EventTarget())
  vi.stubGlobal('CustomEvent', class extends Event { constructor(type, options) { super(type); this.detail = options?.detail } })
})
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

describe('账号切换请求边界', () => {
  it('写请求携带 CSRF，GET 不续写认证，401 清除会话', async () => {
    const runtime = await import('../hooks/sessionRuntime.js')
    runtime.setSessionToken('synthetic-csrf')
    const fetch = vi.fn().mockResolvedValue(new Response('{}'))
    vi.stubGlobal('fetch', fetch)
    await runtime.apiFetch('/api/portfolio', { method: 'POST', headers: { 'Content-Type': 'application/json' } })
    expect(fetch.mock.calls[0][1].headers.get('X-CSRF-Token')).toBe('synthetic-csrf')
    const expired = vi.fn()
    window.addEventListener(runtime.SESSION_ENDED, expired)
    fetch.mockResolvedValueOnce(new Response('{}', { status: 401 }))
    await expect(runtime.apiFetch('/api/portfolio')).rejects.toThrow('登录')
    expect(expired).toHaveBeenCalledOnce()
  })

  it('切换后拒绝前一账号迟到的响应正文并清空预取缓存', async () => {
    const runtime = await import('../hooks/sessionRuntime.js')
    const { prefetchJSON } = await import('../hooks/useApi.js')
    runtime.setSessionToken('account-a')
    let finish
    vi.stubGlobal('fetch', vi.fn().mockImplementationOnce(() => new Promise(resolve => { finish = resolve })))
    const stale = prefetchJSON('/api/private')
    runtime.invalidateSession()
    runtime.setSessionToken('account-b')
    finish(new Response('{"owner":"a"}'))
    await expect(stale).rejects.toThrow()
    fetch.mockResolvedValueOnce(new Response('{"owner":"b"}'))
    expect(await prefetchJSON('/api/private')).toEqual({ owner: 'b' })
  })

  it('响应头已到达但正文未到达时，切换后也拒绝旧正文', async () => {
    const runtime = await import('../hooks/sessionRuntime.js')
    runtime.setSessionToken('a')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(new ReadableStream({ start() {} }))))
    const response = await runtime.apiFetch('/api/private')
    const body = response.json()
    runtime.invalidateSession()
    await expect(body).rejects.toThrow()
  })
})

describe('WebSocket 生命周期', () => {
  it('卸载清理重连与心跳，不接收旧连接消息', async () => {
    vi.useFakeTimers()
    const sockets = []
    vi.stubGlobal('WebSocket', class {
      static OPEN = 1
      readyState = 1
      constructor() { sockets.push(this) }
      send = vi.fn()
      close = vi.fn(() => this.onclose?.())
    })
    const { connectSocket } = await import('../hooks/socketConnection.js')
    const receive = vi.fn()
    const stop = connectSocket('ws://localhost/ws', receive)
    sockets[0].onmessage({ data: '{"type":"price_update"}' })
    expect(receive).toHaveBeenCalledOnce()
    sockets[0].onclose()
    vi.advanceTimersByTime(1000)
    expect(sockets).toHaveLength(2)
    sockets[1].onclose()
    stop()
    sockets[0].onmessage?.({ data: '{"type":"price_update"}' })
    vi.advanceTimersByTime(60000)
    expect(sockets).toHaveLength(2)
    expect(receive).toHaveBeenCalledOnce()
    expect(vi.getTimerCount()).toBe(0)
    vi.useRealTimers()
  })
})

describe('完整响应生命周期', () => {
  it('响应头到达后，调用方仍可取消流', async () => {
    const runtime = await import('../hooks/sessionRuntime.js')
    let nativeSignal
    vi.stubGlobal('fetch', vi.fn(async (_url, options) => {
      nativeSignal = options.signal
      return new Response(new ReadableStream({ start() {} }))
    }))
    const caller = new AbortController()
    const response = await runtime.apiFetch('/api/ask/events', { signal: caller.signal })
    caller.abort()
    expect(nativeSignal.aborted).toBe(true)
    await expect(response.body.cancel()).rejects.toThrow()
  })

  it('账号切换中止已开始的流，并拒绝旧流事件', async () => {
    const runtime = await import('../hooks/sessionRuntime.js')
    let nativeSignal
    vi.stubGlobal('fetch', vi.fn(async (_url, options) => {
      nativeSignal = options.signal
      return new Response(new ReadableStream({ start() {} }))
    }))
    const response = await runtime.apiFetch('/api/ask/events')
    const reader = response.body.getReader()
    const reading = reader.read()
    runtime.invalidateSession()
    expect(nativeSignal.aborted).toBe(true)
    await expect(reading).rejects.toThrow()
  })

  it('fetchJSON 也拒绝旧正文，不广播旧账号写入', async () => {
    const runtime = await import('../hooks/sessionRuntime.js')
    const { fetchJSON, MUTATED_EVENT } = await import('../hooks/useApi.js')
    const mutated = vi.fn()
    window.addEventListener(MUTATED_EVENT, mutated)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(new ReadableStream({ start() {} }))))
    const reading = fetchJSON('/api/portfolio', { method: 'POST' })
    await Promise.resolve()
    runtime.invalidateSession()
    await expect(reading).rejects.toThrow()
    expect(mutated).not.toHaveBeenCalled()
  })

  it('账号切换清除板块持仓说明等私人持久缓存', async () => {
    const runtime = await import('../hooks/sessionRuntime.js')
    const values = new Map([['sectorMatrix_10', '{"ai":{"holdings_note":"A的持仓"}}'], ['sectorMatrix_20', '{}'], ['sentimentAi', '{}'], ['allocTemplate', 'balanced']])
    vi.stubGlobal('localStorage', { removeItem: key => values.delete(key) })
    runtime.invalidateSession()
    expect(values.has('sectorMatrix_10')).toBe(false)
    expect(values.has('sectorMatrix_20')).toBe(false)
    expect(values.has('sentimentAi')).toBe(false)
    expect(values.get('allocTemplate')).toBe('balanced')
  })
})

it('204 响应即使被浏览器桥接为空流也能正常退出', async () => {
  const { fetchJSON } = await import('../hooks/useApi.js')
  const response = new Response('')
  // 部分嵌入式浏览器将无正文响应表示为空流；仍须遵守 204 不接受正文的约束。
  Object.defineProperty(response, 'status', { value: 204 })
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response))
  expect(await fetchJSON('/api/auth/logout', { method: 'POST' })).toEqual({})
})

it('切换账号后不能消费已经预取但尚未读取的旧流数据', async () => {
  const runtime = await import('../hooks/sessionRuntime.js')
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(new ReadableStream({ start(c) { c.enqueue(new TextEncoder().encode('private-from-a')) } }))))
  const response = await runtime.apiFetch('/api/ask/events')
  await new Promise(resolve => setTimeout(resolve, 0))
  runtime.invalidateSession()
  await expect(response.body.getReader().read()).rejects.toThrow()
})
