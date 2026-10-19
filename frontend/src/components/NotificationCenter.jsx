import { useEffect, useState } from 'react'
import { fetchJSON } from '../hooks/useApi'

const STATES = { pending: '待发送', sending: '发送中', retry: '等待同渠道重试', accepted: '渠道已接受', failed: '发送失败', unknown: '结果未知，未自动重发', cancelled: '已取消', interrupted: '恢复中断' }

export function MessageList({ messages, onRead, onResend, activeChannel }) {
  if (!messages.length) return <p className="text-sm text-text-muted">暂无消息。未配置外部渠道也可以接收应用内提醒。</p>
  return <ul className="space-y-3">
    {messages.map(m => <li key={m.id} className="border border-border rounded p-3 space-y-2">
      <div className="flex flex-wrap justify-between gap-2"><strong>{m.title}</strong><span>{m.read_at ? '已读' : '未读'}</span></div>
      <time className="text-xs text-text-muted">{new Date(m.created_at * 1000).toLocaleString()}</time>
      <p className="text-sm whitespace-pre-wrap break-words">{m.body}</p>
      <p className="text-xs text-text-muted">{STATES[m.delivery_state] || '仅应用内'} · 渠道接受不代表已读</p>
      {!m.read_at && <button className="text-accent text-sm" onClick={() => onRead(m.id)}>标为已读</button>}
      {onResend && activeChannel && ['failed', 'unknown', 'cancelled', 'interrupted'].includes(m.delivery_state) && <button className="text-accent text-sm ml-3" onClick={() => onResend(m.id)}>重发至 {activeChannel.name}</button>}
    </li>)}
  </ul>
}

export default function NotificationCenter({ activeChannel }) {
  const [messages, setMessages] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [more, setMore] = useState(true)
  const load = async () => { const data = await fetchJSON('/api/notifications'); setMessages(data.messages) }
  useEffect(() => {
    let live = true
    const refresh = () => fetchJSON('/api/notifications').then(d => { if (live) setMessages(d.messages) }).catch(e => { if (live) setError(e.message) })
    refresh()
    const timer = setInterval(refresh, 30000)
    return () => { live = false; clearInterval(timer) }
  }, [])
  const action = async (id, resend = false) => {
    if (resend && !confirm(`确认重发至「${activeChannel.name}」？原结果未知时可能重复送达。`)) return
    setBusy(true); setError('')
    try {
      await fetchJSON(`/api/notifications/${encodeURIComponent(id)}/${resend ? 'resend' : 'read'}`, { method: 'POST', ...(resend ? { body: JSON.stringify({ channel_id: activeChannel.id, confirmed: true }) } : {}) })
      await load()
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }
  const older = async () => {
    if (!messages?.length) return
    setBusy(true); setError('')
    try {
      const data = await fetchJSON(`/api/notifications?cursor=${encodeURIComponent(messages.at(-1).id)}`)
      setMessages(current => [...current, ...data.messages]); setMore(data.messages.length >= 50)
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }
  return <section aria-labelledby="message-center-title" className="space-y-3" aria-busy={busy}>
    <h3 id="message-center-title" className="text-sm font-semibold">我的消息</h3>
    {error && <p role="alert" className="text-bear text-sm">{error}</p>}
    {messages ? <MessageList messages={messages} activeChannel={activeChannel} onRead={id => action(id)} onResend={id => action(id, true)} /> : <p role="status">正在加载消息…</p>}
    {messages?.length >= 50 && more && <button disabled={busy} className="text-accent text-sm" onClick={older}>查看更早消息</button>}
  </section>
}
