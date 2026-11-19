import { apiFetch, onSessionReset } from './sessionRuntime'

const BASE = ''

// 数据变更广播: 持仓/资产的写操作成功后通知所有关心的组件(顶部 Dashboard 等)
// 立即重拉, 免得各自的轮询周期造成"改完了顶条还是旧数"的观感。
export const MUTATED_EVENT = 'licai:data-mutated'

// GET 预取缓存: 同 URL 短 TTL 内复用同一个 promise——榜单预取光标附近个股的K线,
// 方向键翻股时 ProKline 直接命中, 不再等网络。失败的请求立即出缓存, 下次可重试。
const _prefetched = new Map()
const _aiRequests = new Map()
onSessionReset(() => { _prefetched.clear(); _aiRequests.clear() })
const PREFETCH_TTL = 60_000
export function prefetchJSON(path) {
  const hit = _prefetched.get(path)
  if (hit && Date.now() - hit.t < PREFETCH_TTL) return hit.p
  const p = fetchJSON(path)
  p.catch(() => { if (_prefetched.get(path)?.p === p) _prefetched.delete(path) })
  _prefetched.set(path, { t: Date.now(), p })
  return p
}

export async function fetchJSON(path, options) {
  const res = await apiFetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  const data = res.status === 204 ? {} : await res.json()
  if (res.ok) {
    const method = (options?.method || 'GET').toUpperCase()
    if (method !== 'GET' && /^\/api\/(assets|portfolio)/.test(path)) {
      try { window.dispatchEvent(new CustomEvent(MUTATED_EVENT, { detail: path })) } catch {}
    }
  }
  if (!res.ok) {
    // FastAPI/Pydantic 422: {detail: [{loc, msg, type}, ...]}
    // FastAPI HTTPException: {detail: "..."}
    let msg = `HTTP ${res.status}`
    if (data.detail) {
      if (typeof data.detail === 'string') msg = data.detail
      else if (Array.isArray(data.detail)) {
        msg = data.detail.map(d => `${(d.loc || []).slice(-1)[0] || ''}: ${d.msg}`).join('; ')
      } else msg = data.detail.message || `HTTP ${res.status}`
    }
    throw new Error(msg)
  }
  return data
}

const DAILY_AI_PATHS = new Set([
  '/api/market/sentiment-ai', '/api/market/structure', '/api/sector/trend-ai', '/api/news/digest',
  '/api/news/daily-review', '/api/portfolio/trade-review-ai',
])

// 请求归页面外的会话持有；切回时继续等待同一个 Promise，不再向后端重复提交。
export function fetchAIJSON(path, options) {
  const [pathname, query = ''] = path.split('?')
  if (!DAILY_AI_PATHS.has(pathname) || (options?.method || 'GET').toUpperCase() !== 'GET') return fetchJSON(path, options)
  const parameters = new URLSearchParams(query)
  const force = ['true', '1'].includes(parameters.get('force'))
  parameters.delete('force')
  parameters.sort()
  const key = `${pathname}?${parameters}`
  const day = new Date(Date.now() + 8 * 60 * 60_000).toISOString().slice(0, 10)
  const existing = _aiRequests.get(key)
  if (existing?.day === day && (existing.pending || !force)) return existing.promise

  const entry = { pending: true, day }
  const restorePrevious = () => {
    if (existing?.day === day && existing.success && _aiRequests.get(key) === entry) {
      _aiRequests.set(key, existing)
    }
  }
  entry.promise = fetchJSON(path, options).then(data => {
    entry.pending = false
    entry.success = !data.error
    if (data.error) restorePrevious()
    return data
  }, error => {
    entry.pending = false
    restorePrevious()
    // 失败也保留到当天结束，避免切页自动重试；手动重新生成可立即重试。
    throw error
  })
  _aiRequests.set(key, entry)
  return entry.promise
}

export const api = {
  // Portfolio
  getPortfolio: () => fetchJSON('/api/portfolio'),
  addHolding: (data) => fetchJSON('/api/portfolio', { method: 'POST', body: JSON.stringify(data) }),
  updateHolding: (code, data) => fetchJSON(`/api/portfolio/${code}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteHolding: (code) => fetchJSON(`/api/portfolio/${code}`, { method: 'DELETE' }),

  // Settings
  getFeishuConfig: () => fetchJSON('/api/settings/feishu'),
  saveFeishuConfig: (url) => fetchJSON('/api/settings/feishu', { method: 'POST', body: JSON.stringify({ webhook_url: url }) }),
  testFeishu: () => fetchJSON('/api/settings/feishu/test', { method: 'POST' }),

  // LLM Config
  getLLMConfig: () => fetchJSON('/api/settings/llm'),
  saveLLMConfig: (data) => fetchJSON('/api/settings/llm', { method: 'POST', body: JSON.stringify(data) }),
  testLLM: () => fetchJSON('/api/settings/llm/test', { method: 'POST' }),

  getProxy: () => fetchJSON('/api/settings/proxy'),
  saveProxy: (proxy) => fetchJSON('/api/settings/proxy', { method: 'POST', body: JSON.stringify({ proxy }) }),
  detectProxy: () => fetchJSON('/api/settings/proxy/detect', { method: 'POST' }),
  testProxy: (proxy) => fetchJSON('/api/settings/proxy/test', { method: 'POST', body: JSON.stringify({ proxy }) }),
}
