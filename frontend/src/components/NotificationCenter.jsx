import { useEffect, useRef, useState } from 'react'
import { fetchJSON } from '../hooks/useApi'

const STATES = { pending: '待发送', sending: '发送中', retry: '等待同渠道重试', accepted: '渠道已接受', failed: '发送失败', unknown: '结果未知，未自动重发', cancelled: '已取消', interrupted: '恢复中断' }
const MESSAGE_LIMIT = 50

export function MessageList({ messages, onRead, onResend, activeChannel, selectedIds = [], onSelect, busy = false }) {
  if (!messages.length) return <p className="text-sm text-text-muted">暂无消息。未配置外部渠道也可以接收应用内提醒。</p>
  return <ul className="space-y-3">
    {messages.map(m => <li key={m.id} className="border border-border rounded p-3 space-y-2">
      <div className="flex flex-wrap justify-between gap-2"><div className="flex items-start gap-2 min-w-0">{onSelect && <input type="checkbox" aria-label={`选择通知：${m.title}`} checked={selectedIds.includes(m.id)} disabled={busy} onChange={() => onSelect(m.id)} className="mt-1 shrink-0" />}<strong className="break-words min-w-0">{m.title}</strong></div><span>{m.read_at ? '已读' : '未读'}</span></div>
      <time className="text-xs text-text-muted">{new Date(m.created_at * 1000).toLocaleString()}</time>
      <p className="text-sm whitespace-pre-wrap break-words">{m.body}</p>
      <p className="text-xs text-text-muted">{STATES[m.delivery_state] || '仅应用内'} · 渠道接受不代表已读</p>
      {!m.read_at && <button disabled={busy} className="text-accent text-sm disabled:opacity-50" onClick={() => onRead(m.id)}>标为已读</button>}
      {onResend && activeChannel && ['failed', 'unknown', 'cancelled', 'interrupted'].includes(m.delivery_state) && <button disabled={busy} className="text-accent text-sm ml-3 disabled:opacity-50" onClick={() => onResend(m.id)}>重发至 {activeChannel.name}</button>}
    </li>)}
  </ul>
}

export default function NotificationCenter({ activeChannel }) {
  const [messages, setMessages] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [selectedIds, setSelectedIds] = useState([])
  const selectAllRef = useRef(null)
  const visibleSelectedIds = selectedIds.filter(id => messages?.some(m => m.id === id))
  const allSelected = !!messages?.length && visibleSelectedIds.length === messages.length
  const unreadSelectedIds = messages?.filter(m => !m.read_at && selectedIds.includes(m.id)).map(m => m.id) || []
  useEffect(() => {
    if (selectAllRef.current) selectAllRef.current.indeterminate = visibleSelectedIds.length > 0 && !allSelected
  }, [visibleSelectedIds.length, allSelected])
  const load = async () => { const data = await fetchJSON('/api/notifications'); setMessages(data.messages.slice(0, MESSAGE_LIMIT)) }
  useEffect(() => {
    let live = true
    const refresh = () => fetchJSON('/api/notifications').then(data => {
      if (!live) return
      const latest = data.messages.slice(0, MESSAGE_LIMIT)
      setMessages(latest)
      setSelectedIds(ids => ids.filter(id => latest.some(m => m.id === id)))
    }).catch(e => { if (live) setError(e.message) })
    refresh()
    const timer = setInterval(refresh, 30000)
    return () => { live = false; clearInterval(timer) }
  }, [])
  const action = async (id, resend = false) => {
    if (busy) return
    if (resend && !confirm(`确认重发至「${activeChannel.name}」？原结果未知时可能重复送达。`)) return
    setBusy(true); setError('')
    try {
      await fetchJSON(`/api/notifications/${encodeURIComponent(id)}/${resend ? 'resend' : 'read'}`, { method: 'POST', ...(resend ? { body: JSON.stringify({ channel_id: activeChannel.id, confirmed: true }) } : {}) })
      await load()
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }
  const markSelectedRead = async () => {
    if (busy || !unreadSelectedIds.length) return
    setBusy(true); setError('')
    try {
      await fetchJSON('/api/notifications/read', { method: 'POST', body: JSON.stringify({ ids: unreadSelectedIds }) })
      await load()
      setSelectedIds([])
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }
  const deleteSelected = async () => {
    if (busy || !visibleSelectedIds.length) return
    if (!confirm(`确认删除选中的 ${visibleSelectedIds.length} 条通知？删除后无法恢复。`)) return
    setBusy(true); setError('')
    try {
      await fetchJSON('/api/notifications/delete', { method: 'POST', body: JSON.stringify({ ids: visibleSelectedIds }) })
      await load()
      setSelectedIds([])
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }
  return <section aria-labelledby="message-center-title" className="space-y-3" aria-busy={busy}>
    <h3 id="message-center-title" className="text-sm font-semibold">我的消息</h3>
    <p className="text-xs text-text-muted">最多显示最近 {MESSAGE_LIMIT} 条通知</p>
    {!!messages?.length && <div className="flex flex-wrap items-center gap-3 text-sm">
      <label className="flex items-center gap-2"><input ref={selectAllRef} type="checkbox" checked={allSelected} disabled={busy} onChange={e => setSelectedIds(e.target.checked ? messages.map(m => m.id) : [])} />全选</label>
      <span className="text-text-muted">已选 {visibleSelectedIds.length} 条</span>
      <button disabled={busy || !unreadSelectedIds.length} className="text-accent disabled:opacity-50" onClick={markSelectedRead}>一键已读</button>
      <button disabled={busy || !visibleSelectedIds.length} className="text-bear disabled:opacity-50" onClick={deleteSelected}>一键删除</button>
    </div>}
    {error && <p role="alert" className="text-bear text-sm">{error}</p>}
    {messages ? <MessageList messages={messages} activeChannel={activeChannel} selectedIds={visibleSelectedIds} onSelect={id => setSelectedIds(ids => ids.includes(id) ? ids.filter(value => value !== id) : [...ids, id])} busy={busy} onRead={id => action(id)} onResend={id => action(id, true)} /> : <p role="status">正在加载消息…</p>}
  </section>
}
