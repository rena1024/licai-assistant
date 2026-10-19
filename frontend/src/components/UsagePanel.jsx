import { useEffect, useState } from 'react'
import { fetchJSON } from '../hooks/useApi'

const yuan = value => value == null ? '未配置' : `¥${(value / 1000000).toFixed(6)}`

export function BudgetSummary({ data }) {
  return <div className="grid gap-3 sm:grid-cols-2">
    {Object.entries(data).map(([scope, periods]) => <div key={scope} className="rounded border border-border p-3">
      <h4 className="text-[12px] font-semibold">{scope === 'user' ? '本人预算' : '全局预算'}</h4>
      {Object.entries(periods).map(([period, row]) => <p key={period} className="my-2 text-sm">
        {period === 'day' ? '当日' : '当月'} · 上限 {yuan(row.limit)}<br />
        已结算估算 {yuan(row.spent)} · 占用 {yuan(row.reserved)} · 可用 {yuan(row.available)}
      </p>)}
    </div>)}
  </div>
}

export default function UsagePanel({ admin }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [global, setGlobal] = useState(false)
  const [busy, setBusy] = useState(false)
  const [cloud, setCloud] = useState(null)
  const [notice, setNotice] = useState('')
  const [policy, setPolicy] = useState(null)
  const load = async () => setData(await fetchJSON(`/api/usage${global ? '?subject=0' : ''}`))
  useEffect(() => {
    let active = true
    setData(null)
    fetchJSON(`/api/usage${global ? '?subject=0' : ''}`).then(result => { if (active) setData(result) }).catch(e => { if (active) setError(e.message) })
    return () => { active = false }
  }, [global])
  useEffect(() => {
    let active = true
    fetchJSON('/api/usage/cloud-state').then(result => { if (active) setCloud(result) }).catch(e => { if (active) setError(e.message) })
    return () => { active = false }
  }, [])
  useEffect(() => {
    if (!admin) return
    let active = true
    fetchJSON('/api/usage/policy').then(result => { if (active) setPolicy(result) }).catch(e => { if (active) setError(e.message) })
    return () => { active = false }
  }, [admin])
  const manage = async (event, path, body) => {
    event.preventDefault(); setBusy(true); setError(''); setNotice('')
    try {
      await fetchJSON(path, { method: path.endsWith('/policy') ? 'PUT' : 'POST', body: JSON.stringify(body) })
      setNotice('已保存核对记录或策略')
      await load(); setCloud(await fetchJSON('/api/usage/cloud-state'))
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }
  const save = async event => {
    event.preventDefault(); setBusy(true); setError('')
    const values = Object.fromEntries(new FormData(event.currentTarget))
    try {
      await fetchJSON('/api/usage/limits', { method: 'PUT', body: JSON.stringify({ subject: Number(values.subject), day: values.day || null, month: values.month || null }) })
      await load()
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }
  return <section className="border-t border-border pt-4 space-y-3" aria-labelledby="usage-title">
    <h3 id="usage-title" className="text-sm font-semibold">用量与预算</h3>
    <p className="text-xs text-text-dim">费用为估算值，以厂商账单为准。待核实费用继续占用预算；明细默认保留 7 天，月汇总长期保留。</p>
    {admin && <label><input type="checkbox" checked={global} onChange={e => setGlobal(e.target.checked)} /> 查看全局计量元数据</label>}
    {data ? <>
      <BudgetSummary data={data.budget} />
      {data.alerts.map(a => <p key={`${a.subject}-${a.kind}-${a.period}-${a.threshold}`} role="status">{a.period} {a.kind === 'day' ? '日' : '月'}预算已达到 {a.threshold}%</p>)}
      <h4 className="text-[12px] font-semibold">待结算及待核实</h4>
      {data.pending.length === 0 ? <p className="text-sm">暂无</p> : data.pending.map(a => <div key={a.id} className="border border-border rounded p-2 text-sm"><p>{a.model_id} · {a.state === 'unknown' ? '待核实' : '进行中预留'} · {yuan(a.reserved)}</p>
        {admin && a.state === 'unknown' && <details><summary>人工核对</summary><form onSubmit={event => {
          const values = Object.fromEntries(new FormData(event.currentTarget))
          const usage = Object.fromEntries(['input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens', 'reasoning_tokens'].map(k => [k, Number(values[k] || 0)]))
          manage(event, `/api/usage/reconcile/${a.id}`, { usage, reason: values.reason })
        }} className="space-y-2">
          <p className="text-xs">依据厂商实际记录核对 Token；输入包含缓存，输出包含推理，细分不重复计费。</p>
          {Object.entries({ input_tokens: '总输入 Token', output_tokens: '总输出 Token', cache_read_tokens: '缓存读取 Token', cache_write_tokens: '缓存写入 Token', reasoning_tokens: '推理 Token' }).map(([key, label]) => <label key={key} className="block">{label}<input name={key} type="number" min="0" step="1" required defaultValue="0" className="ml-2 border border-border bg-bg p-1" /></label>)}
          <label className="block">核对依据<input name="reason" required minLength={5} maxLength={500} className="w-full border border-border bg-bg p-1" /></label>
          <button disabled={busy} className="text-accent">确认核对并结算</button>
        </form></details>}
      </div>)}
      <h4 className="text-[12px] font-semibold">近期调用</h4>
      {data.details.length === 0 ? <p className="text-sm">暂无调用明细</p> : <div className="overflow-x-auto"><table className="w-full text-sm"><thead><tr><th>场景 / 模型</th><th>输入 / 输出 Token</th><th>状态</th><th>估算费用</th></tr></thead><tbody>
        {data.details.map(d => <tr key={d.id}><td>{d.scene} / {d.model_id}</td><td>{d.input_tokens ?? '未知'} / {d.output_tokens ?? '未知'}</td><td>{d.state}</td><td>{yuan(d.cost)}</td></tr>)}
      </tbody></table></div>}
      <h4 className="text-[12px] font-semibold">月汇总</h4>
      {data.monthly.map(m => <p key={`${m.month}-${m.model_id}-${m.scene}`} className="text-sm">{m.month} · {m.scene} / {m.model_id} · {m.calls} 次 · {m.input_tokens} 输入 / {m.output_tokens} 输出 Token</p>)}
    </> : <p role="status">正在读取用量…</p>}
    {admin && <form onSubmit={save} className="space-y-2">
      <h4 className="text-[12px] font-semibold">配置额度（人民币）</h4>
      <label className="block">用户编号（0 为全局）<input name="subject" type="number" required min="0" defaultValue="0" className="ml-2 border border-border bg-bg p-1" /></label>
      <label className="block">日额度<input name="day" type="number" min="0" step="0.000001" className="ml-2 border border-border bg-bg p-1" /></label>
      <label className="block">月额度<input name="month" type="number" min="0" step="0.000001" className="ml-2 border border-border bg-bg p-1" /></label>
      <p className="text-xs">留空表示未配置，0 表示无云端额度。保存不会清除已用费用。</p>
      <button disabled={busy} className="rounded bg-accent px-3 py-2 text-bg">保存额度</button>
    </form>}
    {admin && <>
      <p role="status">云端调用：{cloud?.held ? '暂停，等待管理员核对并确认' : '按模型配置和四项预算逐次授权'}</p>
      <form onSubmit={event => {
        const values = Object.fromEntries(new FormData(event.currentTarget))
        manage(event, '/api/usage/cloud-confirmation', { reason: values.reason })
      }} className="space-y-2">
        <label className="block">云端开放／恢复核对依据<input name="reason" required minLength={5} maxLength={500} className="w-full border border-border bg-bg p-1" /></label>
        <p className="text-xs">先核对恢复前后费用及预算，处理全部待核实记录。确认不清空已用费用，模型和个人选择仍须另行配置。</p>
        <button disabled={busy} className="rounded border border-border px-3 py-2">确认费用并允许受控云端调用</button>
      </form>
      {policy && <form onSubmit={event => {
        const values = Object.fromEntries(new FormData(event.currentTarget))
        manage(event, '/api/usage/policy', { detail_days: Number(values.days), warning_threshold: Number(values.threshold), local_concurrency: Number(values.concurrency) })
      }} className="space-y-2">
        <label className="block">调用明细保留天数<input name="days" type="number" required min="1" max="365" defaultValue={policy.detail_days} className="ml-2 border border-border bg-bg p-1" /></label>
        <label className="block">预算预警百分比<input name="threshold" type="number" required min="1" max="99" defaultValue={policy.warning_threshold} className="ml-2 border border-border bg-bg p-1" /></label>
        <label className="block">全局本地推理并发<input name="concurrency" type="number" required min="1" max="8" defaultValue={policy.local_concurrency} className="ml-2 border border-border bg-bg p-1" /></label>
        <button disabled={busy} className="text-accent">保存用量策略</button>
      </form>}
    </>}
    {notice && <p role="status">{notice}</p>}
    {error && <p role="alert">{error}</p>}
  </section>
}
