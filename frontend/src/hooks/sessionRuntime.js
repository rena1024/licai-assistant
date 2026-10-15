// 所有 API（含直接读取流的页面）共用身份代际，拒绝切换后的旧响应。
export const SESSION_ENDED = 'licai:session-ended'
let generation = 0
let csrfToken = ''
const pending = new Set()
const resetters = new Set()

export function onSessionReset(reset) {
  resetters.add(reset)
  return () => resetters.delete(reset)
}

export function invalidateSession() {
  generation += 1
  csrfToken = ''
  for (const controller of pending) controller.abort()
  pending.clear()
  for (const reset of resetters) reset()
  try {
    for (const key of ['sentimentAi', 'sectorMatrix_10', 'sectorMatrix_20', 'licai.wl.group']) localStorage.removeItem(key)
  } catch {}
  window.dispatchEvent(new CustomEvent(SESSION_ENDED))
}

export function setSessionToken(token) { csrfToken = token }

export async function apiFetch(path, options = {}) {
  const epoch = generation
  const controller = new AbortController()
  const headers = new Headers(options.headers)
  if (!['GET', 'HEAD', 'OPTIONS'].includes((options.method || 'GET').toUpperCase()) && csrfToken) {
    headers.set('X-CSRF-Token', csrfToken)
  }
  const abort = () => controller.abort()
  options.signal?.addEventListener('abort', abort, { once: true })
  if (options.signal?.aborted) controller.abort()
  pending.add(controller)
  const checkCurrent = () => {
    if (epoch !== generation) throw new Error('账号已切换，请重新加载')
  }
  let releaseStream = () => {}
  const cleanup = () => {
    releaseStream()
    pending.delete(controller)
    options.signal?.removeEventListener('abort', abort)
  }
  try {
    const response = await fetch(path, { ...options, headers, signal: controller.signal, credentials: 'same-origin', cache: 'no-store' })
    checkCurrent()
    if (response.status === 401 && !path.startsWith('/api/auth/')) {
      invalidateSession()
      throw new Error('登录已失效，请重新登录')
    }
    // 控制器必须持续到流读完，不能在响应头到达时解除调用方的取消监听。
    let guarded = response
    if (response.body && ![204, 205, 304].includes(response.status)) {
      const reader = response.body.getReader()
      let streamController
      const abortStream = () => {
        streamController.error(new DOMException('请求已取消', 'AbortError'))
        void reader.cancel().catch(() => {})
        cleanup()
      }
      const stream = new ReadableStream({
        start(target) { streamController = target },
        async pull(target) {
          try {
            const { done, value } = await reader.read()
            checkCurrent()
            if (controller.signal.aborted) throw new DOMException('请求已取消', 'AbortError')
            if (done) { cleanup(); target.close() }
            else target.enqueue(value)
          } catch (error) { cleanup(); target.error(error); await reader.cancel().catch(() => {}) }
        },
        cancel(reason) { cleanup(); return reader.cancel(reason) },
      })
      controller.signal.addEventListener('abort', abortStream, { once: true })
      releaseStream = () => controller.signal.removeEventListener('abort', abortStream)
      if (controller.signal.aborted) abortStream()
      guarded = new Response(stream, { status: response.status, statusText: response.statusText, headers: response.headers })
    } else cleanup()
    // 正文可能比响应头晚到达，因此读取完正文后再次校验代际。
    for (const method of ['json', 'text', 'blob', 'arrayBuffer']) {
      const read = guarded[method].bind(guarded)
      guarded[method] = async (...args) => {
        const value = await read(...args)
        checkCurrent()
        return value
      }
    }
    return guarded
  } catch (error) {
    cleanup()
    throw error
  }
}
