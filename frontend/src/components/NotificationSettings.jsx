import { useEffect, useState } from 'react'
import { fetchJSON } from '../hooks/useApi'
import NotificationCenter from './NotificationCenter'

const inputClass = 'w-full bg-bg border border-border rounded px-3 py-2 text-sm'
const buttonClass = 'px-3 py-1.5 rounded border border-border text-sm disabled:opacity-50'
const NAMES = { pushplus: 'PushPlus（推荐）', email: '邮件 SMTP', feishu: '飞书 Webhook' }
const FIELDS = {
  pushplus: [['token', 'PushPlus Token', 'password']],
  feishu: [['webhook_url', '飞书 Webhook URL', 'password']],
  email: [['host', 'SMTP 主机', 'text'], ['username', 'SMTP 账号', 'text'], ['password', 'SMTP 授权码', 'password'], ['from_address', '发件地址', 'email'], ['to_address', '收件地址', 'email']],
}

export function ChannelFields({ kind, config, onChange, editing = false }) {
  return <div className="grid gap-3 sm:grid-cols-2">
    {(FIELDS[kind] || []).map(([key, label, type]) => <label key={key} className="text-xs space-y-1">{label}
      <input className={inputClass} type={type} autoComplete="off" value={config[key] || ''} required={type !== 'password' || !editing} placeholder={type === 'password' && editing ? '留空保留已保存凭证' : ''} onChange={e => onChange({ ...config, [key]: e.target.value })} />
    </label>)}
    {kind === 'email' && <label className="text-xs space-y-1">安全连接
      <select className={inputClass} value={config.security || 'tls'} onChange={e => onChange({ ...config, security: e.target.value, port: e.target.value === 'tls' ? 465 : 587 })}>
        <option value="tls">TLS · 465</option><option value="starttls">STARTTLS · 587</option>
      </select>
    </label>}
  </div>
}

export default function NotificationSettings({ admin = false }) {
  const [channels, setChannels] = useState(null)
  const [hold, setHold] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [form, setForm] = useState({ kind: 'pushplus', name: '', config: {} })
  const [editing, setEditing] = useState(null)
  const load = async () => { const d = await fetchJSON('/api/notifications/channels'); setChannels(d.channels); setHold(d.notification_hold) }
  useEffect(() => {
    let live = true
    fetchJSON('/api/notifications/channels').then(d => { if (live) { setChannels(d.channels); setHold(d.notification_hold) } }).catch(e => { if (live) setError(e.message) })
    return () => { live = false }
  }, [])
  const run = async fn => {
    setBusy(true); setError(''); setNotice('')
    try { await fn(); await load() } catch (e) { setError(e.message) } finally { setBusy(false) }
  }
  const active = channels?.find(c => c.active)
  const save = e => {
    e.preventDefault()
    run(async () => {
      const config = form.kind === 'email' ? { security: 'tls', port: 465, ...form.config } : form.config
      await fetchJSON('/api/notifications/channels', { method: 'PUT', body: JSON.stringify({ ...form, config, ...(editing ? { id: editing.id } : {}), expected_version: editing?.version || 0 }) })
      setForm({ kind: 'pushplus', name: '', config: {} }); setEditing(null); setNotice('已保存；保存不会发送测试，也不会自动启用。')
    })
  }
  const activate = id => run(() => fetchJSON('/api/notifications/active', { method: 'PUT', body: JSON.stringify({ channel_id: id }) }))
  const test = () => {
    if (!confirm(`确认发送测试摘要至「${active.name}」？`)) return
    run(async () => { await fetchJSON('/api/notifications/test', { method: 'POST', body: JSON.stringify({ channel_id: active.id, confirmed: true }) }); setNotice('测试已排队，请在消息中心查看渠道发送结果。') })
  }
  return <section aria-labelledby="push-settings-title" className="border-t border-border pt-4 space-y-4">
    <h3 id="push-settings-title" className="text-sm font-semibold">推送设置</h3>
    <p className="text-xs text-text-muted">可保存多个配置，每次只启用一个，所有外发事件共用当前渠道。外发仅摘要，详情在应用内查看。局域网链接需网络可达。</p>
    {error && <p role="alert" className="text-bear text-sm">{error}</p>}
    {notice && <p role="status" className="text-sm text-accent">{notice}</p>}
    {hold && <p role="status" className="text-sm">备份恢复后外部通知已暂停，旧投递不会自动重发。{admin && <button className={buttonClass} disabled={busy} onClick={() => {
      if (confirm('确认配置与收件人后恢复外部通知？仅后续新消息会发送。')) run(() => fetchJSON('/api/notifications/restore-confirmation', { method: 'POST' }))
    }}>确认收件人并恢复外发</button>}</p>}
    {!channels ? <p role="status">正在加载通知渠道…</p> : <div className="space-y-2">
      {!active && <p className="text-sm">外部通知待配置，可稍后完成，不影响理财或 AI。</p>}
      {channels.map(c => <div key={c.id} className="border border-border rounded p-3 flex flex-wrap items-center gap-3">
        <span className="text-sm">{c.name} · {NAMES[c.kind]} · {c.active ? '当前启用' : '未启用'} · 凭证已保存</span>
        <button className={buttonClass} disabled={busy} onClick={() => activate(c.active ? null : c.id)}>{c.active ? '停用外发' : '启用'}</button>
        <button className={buttonClass} disabled={busy} onClick={() => { setEditing(c); setForm({ kind: c.kind, name: c.name, config: c.fields || {} }) }}>编辑</button>
        {!c.active && <button className={buttonClass} disabled={busy} onClick={() => {
          if (confirm(`删除「${c.name}」并取消其待发送消息？`)) run(() => fetchJSON(`/api/notifications/channels/${encodeURIComponent(c.id)}`, { method: 'DELETE' }))
        }}>删除</button>}
      </div>)}
      {active && <button className={buttonClass} disabled={busy || hold} onClick={test}>发送测试至 {active.name}</button>}
    </div>}
    <form onSubmit={save} className="space-y-3 border border-border rounded p-3">
      <h4 className="text-sm">{editing ? '编辑渠道' : '新增外部渠道'}</h4>
      <label className="block text-xs">渠道类型<select className={inputClass} disabled={!!editing} value={form.kind} onChange={e => setForm({ ...form, kind: e.target.value, config: {} })}>{Object.entries(NAMES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
      <label className="block text-xs">配置名称<input className={inputClass} value={form.name} maxLength={80} required onChange={e => setForm({ ...form, name: e.target.value })} /></label>
      <ChannelFields kind={form.kind} config={form.config} editing={!!editing} onChange={config => setForm({ ...form, config })} />
      <button className={buttonClass} disabled={busy}>{busy ? '保存中…' : '保存渠道'}</button>
      {editing && <button type="button" className={buttonClass} onClick={() => { setEditing(null); setForm({ kind: 'pushplus', name: '', config: {} }) }}>取消编辑</button>}
    </form>
    {admin && <NotificationRetention />}
    <NotificationCenter activeChannel={active} />
  </section>
}

function NotificationRetention() {
  const [days, setDays] = useState(null)
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    let live = true
    fetchJSON('/api/notifications/policy').then(d => { if (live) setDays(d.days) }).catch(e => { if (live) setMessage(e.message) })
    return () => { live = false }
  }, [])
  const save = async e => {
    e.preventDefault(); setBusy(true)
    try { await fetchJSON('/api/notifications/policy', { method: 'PUT', body: JSON.stringify({ days: Number(days) }) }); setMessage('消息保留策略已保存；事件去重与费用依据不受清理影响。') } catch (e) { setMessage(e.message) } finally { setBusy(false) }
  }
  return <form className="space-y-2" onSubmit={save}>
    <label className="text-xs">应用内消息保留天数（管理员）<input className={inputClass} type="number" min="1" max="3650" required value={days ?? ''} onChange={e => setDays(e.target.value)} /></label>
    <button className={buttonClass} disabled={busy || days == null}>保存消息保留策略</button>
    {message && <p role="status" className="text-sm">{message}</p>}
  </form>
}
