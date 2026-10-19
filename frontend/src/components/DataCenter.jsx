import { useEffect, useState } from 'react'
import { fetchJSON } from '../hooks/useApi'

const input = 'bg-bg border border-border rounded px-2 py-1.5 text-sm w-full'
const button = 'border border-border rounded px-3 py-1.5 text-sm disabled:opacity-50'
const providerNames = { sina: '新浪', tencent: '腾讯', eastmoney: '东方财富', em: '东方财富', cls: '财联社', ths: '同花顺', jin10: '金十' }

export function SearchDisclosure({ enabled, onChange }) {
  return <div className="space-y-2 text-sm">
    <label className="flex gap-2"><input type="checkbox" checked={enabled} onChange={e => onChange(e.target.checked)} />允许本人问答使用免费在线搜索</label>
    <p className="text-xs text-text-muted">搜索独立于模型；使用本地 Ollama 时也可检索。仅必要公开查询词会离开本机，不发送账户、持仓数量与成本。免费额度未知或耗尽会暂停，行情与资讯仍可使用。</p>
  </div>
}

export function SearchPreferences() {
  const [enabled, setEnabled] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    let live = true
    fetchJSON('/api/search/preferences').then(d => { if (live) setEnabled(d.enabled) }).catch(e => { if (live) setError(e.message) })
    return () => { live = false }
  }, [])
  const save = async value => {
    const previous = enabled
    setEnabled(value); setBusy(true); setError('')
    try { const d = await fetchJSON('/api/search/preferences', { method: 'PUT', body: JSON.stringify({ enabled: value }) }); setEnabled(d.enabled) } catch (e) { setEnabled(previous); setError(e.message) } finally { setBusy(false) }
  }
  return <section className="border-t border-border pt-4 space-y-3" aria-labelledby="search-preferences-title" aria-busy={busy}>
    <h3 id="search-preferences-title" className="text-sm font-semibold">我的搜索偏好</h3>
    {error && <p role="alert" className="text-sm text-bear">{error}</p>}
    {enabled == null ? <p role="status">加载搜索偏好…</p> : <fieldset disabled={busy}><SearchDisclosure enabled={enabled} onChange={save} /></fieldset>}
  </section>
}

export function SourcePolicyForm({ policy, onChange }) {
  return <div className="space-y-3">
    {Object.entries(policy.quotes).map(([market, order]) => <label key={market} className="block text-xs">{market} 股实时价
      <select aria-label={`${market} 股实时价`} className={input} value={order.join(',')} onChange={e => onChange({ ...policy, quotes: { ...policy.quotes, [market]: e.target.value ? e.target.value.split(',') : [] } })}>
        <option value="sina,tencent">新浪主 · 腾讯独立备用</option><option value="tencent,sina">腾讯主 · 新浪独立备用</option>
        <option value="sina">仅新浪</option><option value="tencent">仅腾讯</option><option value="">停用此市场报价</option>
      </select>
    </label>)}
    <label className="block text-xs">A 股日 K（前复权，独立于实时价）<select className={input} value={policy.kline.join(',')} onChange={e => onChange({ ...policy, kline: e.target.value ? e.target.value.split(',') : [] })}>
      <option value="eastmoney,tencent">东方财富主 · 腾讯备</option><option value="tencent,eastmoney">腾讯主 · 东方财富备</option><option value="eastmoney">仅东方财富</option><option value="tencent">仅腾讯</option><option value="">停用在线日 K</option>
    </select></label>
    <fieldset className="space-y-2"><legend className="text-xs">资讯聚合（并行抓取，不作线性备用）</legend>
      {['em', 'cls', 'ths', 'jin10'].map(id => <label key={id} className="inline-flex gap-1 mr-3 text-xs"><input type="checkbox" checked={policy.news.includes(id)} onChange={e => onChange({ ...policy, news: e.target.checked ? [...policy.news, id] : policy.news.filter(v => v !== id) })} />{providerNames[id]}</label>)}
    </fieldset>
  </div>
}

export function SearchChannelForm({ provider, saved, onSaved }) {
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const save = async e => {
    e.preventDefault(); setBusy(true); setError('')
    const f = new FormData(e.currentTarget)
    const c = { provider, key: String(f.get('key') || ''), enabled: f.has('enabled'), remaining: f.get('remaining') === '' ? null : Number(f.get('remaining')), credits_per_request: Number(f.get('credits_per_request')), period: f.get('period'), verified_until: Math.floor(new Date(f.get('verified_until')).getTime() / 1000) || 0, evidence: f.get('evidence') }
    for (const field of ['free_confirmed', 'auto_pay_disabled', 'exclusive_key', 'storage_allowed']) c[field] = f.has(field)
    try { await fetchJSON('/api/search/channels', { method: 'PUT', body: JSON.stringify({ ...c, expected_version: saved?.version || 0 }) }); await onSaved() } catch (e) { setError(e.message) } finally { setBusy(false) }
  }
  const checks = { free_confirmed: '已核实当前免费计划', auto_pay_disabled: '已关闭自动付费且不存在自动扣费路径', exclusive_key: '凭证仅本应用使用，余额可准确核验', storage_allowed: '条款允许保存问答引用的链接和摘要' }
  return <form onSubmit={save} className="border border-border rounded p-3 space-y-3">
    <h4 className="text-sm">{provider === 'tavily' ? 'Tavily（首选）' : 'Exa（备用）'} · {saved?.paused ? '因结果未知暂停' : saved?.enabled ? '已配置开放' : '未开放'}</h4>
    <p className="text-xs">可用 {saved?.available ?? '未知'} credits；额度独立于模型 Token，未核实免费条件时保持禁用。10 次/分钟、100 次/日/用户。</p>
    <div className="grid gap-3 sm:grid-cols-2">
      <label className="text-xs">API Key<input className={input} type="password" name="key" autoComplete="off" placeholder={saved?.configured ? '已保存，留空保留' : '输入凭证'} /></label>
      <label className="text-xs">本周期可分配免费总额（含本应用已用）<input className={input} name="remaining" type="number" min="0" defaultValue={saved?.remaining ?? ''} /></label>
      <label className="text-xs">每次保守预留 credits<input className={input} name="credits_per_request" type="number" min="1" required defaultValue={saved?.credits_per_request || 1} /></label>
      <label className="text-xs">额度周期（月）<input className={input} name="period" type="month" required defaultValue={saved?.period || new Date().toLocaleDateString('sv-SE', { timeZone: 'Asia/Shanghai' }).slice(0, 7)} /></label>
      <label className="text-xs">余额核验有效至<input className={input} name="verified_until" type="datetime-local" required defaultValue={saved?.verified_until ? new Date(saved.verified_until * 1000 - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16) : ''} /></label>
      <label className="text-xs">核验依据（不要填凭证）<input className={input} name="evidence" maxLength={1000} defaultValue={saved?.evidence || ''} /></label>
    </div>
    {Object.entries(checks).map(([key, label]) => <label key={key} className="flex gap-2 text-xs"><input name={key} type="checkbox" defaultChecked={saved?.[key] || false} />{label}</label>)}
    <label className="flex gap-2 text-sm"><input name="enabled" type="checkbox" defaultChecked={saved?.enabled || false} />开放免费搜索通道</label>
    {error && <p role="alert" className="text-bear text-sm">{error}</p>}
    <button className={button} disabled={busy}>{busy ? '保存中…' : `保存 ${provider} 配置`}</button>
  </form>
}

export default function DataCenter() {
  const [data, setData] = useState(null)
  const [channels, setChannels] = useState([])
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const load = async () => { const [d, s] = await Promise.all([fetchJSON('/api/settings/data-center'), fetchJSON('/api/search/channels')]); setData(d); setChannels(s.channels) }
  useEffect(() => {
    let live = true
    Promise.all([fetchJSON('/api/settings/data-center'), fetchJSON('/api/search/channels')]).then(([d, s]) => { if (live) { setData(d); setChannels(s.channels) } }).catch(e => { if (live) setError(e.message) })
    return () => { live = false }
  }, [])
  const save = async e => {
    e.preventDefault(); setBusy(true); setError(''); setNotice('')
    try { await fetchJSON('/api/settings/data-center', { method: 'PUT', body: JSON.stringify({ ...data.policy, expected_version: data.version }) }); await load(); setNotice('来源策略已保存') } catch (e) { setError(e.message) } finally { setBusy(false) }
  }
  return <section className="border-t border-border pt-4 space-y-4" aria-labelledby="data-center-title">
    <h3 id="data-center-title" className="text-sm font-semibold">管理：数据中心</h3>
    {error && <p role="alert" className="text-bear text-sm">{error}</p>}{notice && <p role="status">{notice}</p>}
    {!data ? <p role="status">正在加载公共来源…</p> : <>
      <p className="text-xs text-text-muted">{data.coverage_note}</p>
      <form onSubmit={save} className="space-y-3"><SourcePolicyForm policy={data.policy} onChange={policy => setData({ ...data, policy })} /><button className={button} disabled={busy}>保存来源策略</button></form>
      <div className="overflow-x-auto"><table className="text-xs w-full"><caption className="text-left py-2">最近来源状态（无记录表示尚未检查）</caption><thead><tr>{['能力/市场', '供应商', '状态', '最近尝试', '最近成功', '源时间'].map(t => <th className="text-left p-2" key={t}>{t}</th>)}</tr></thead><tbody>{data.status.map(s => <tr key={`${s.capability}:${s.provider}:${s.market}`}><td className="p-2">{s.capability}/{s.market}</td><td>{providerNames[s.provider] || s.provider}</td><td>{s.available ? '可用' : s.error}</td><td>{new Date(s.last_attempt * 1000).toLocaleString()}</td><td>{s.last_success ? new Date(s.last_success * 1000).toLocaleString() : '无'}</td><td>{s.source_at || '未知'}</td></tr>)}</tbody></table></div>
    </>}
    {['tavily', 'exa'].map(provider => <SearchChannelForm key={`${provider}:${channels.find(c => c.provider === provider)?.version || 0}`} provider={provider} saved={channels.find(c => c.provider === provider)} onSaved={async () => { await load(); setNotice('搜索配置已保存，未发送真实测试请求') }} />)}
  </section>
}
