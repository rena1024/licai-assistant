import { settingsButton } from './settings/buttonStyles'
import SaveStatus from './settings/SaveStatus'
import { useEffect, useId, useState } from 'react'
import { fetchJSON } from '../hooks/useApi'

const yuan = value => value == null ? '未配置' : `¥${(value / 1000000).toFixed(6)}`
const RECENT_CALLS_PREVIEW = 20

export function RecentCalls({ details }) {
  const [expanded, setExpanded] = useState(false)
  const tableId = useId()
  const visible = expanded ? details : details.slice(0, RECENT_CALLS_PREVIEW)
  return <div className="space-y-2">
    <h4 className="text-[12px] font-semibold">近期调用</h4>
    {!details.length ? <p className="text-sm">暂无调用明细</p> : <div className="overflow-x-auto">
      <table id={tableId} className="w-full text-left text-sm [&_th]:text-left">
        <caption className="sr-only">近期调用记录</caption>
        <thead><tr><th>场景 / 模型</th><th>输入 / 输出 Token</th><th>状态</th><th>估算费用</th></tr></thead>
        <tbody>{visible.map(d => <tr key={d.id}><td>{d.scene} / {d.model_id}</td><td>{d.input_tokens ?? '未知'} / {d.output_tokens ?? '未知'}</td><td>{d.state}</td><td>{yuan(d.cost)}</td></tr>)}</tbody>
      </table>
    </div>}
    {details.length > RECENT_CALLS_PREVIEW && <button type="button" className={settingsButton.secondary} aria-expanded={expanded} aria-controls={tableId} onClick={() => setExpanded(value => !value)}>
      {expanded ? `收起（仅显示 ${RECENT_CALLS_PREVIEW} 条）` : `展开全部（共 ${details.length} 条）`}
    </button>}
  </div>
}

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

function BudgetLimits({ onSaved }) {
  const [subject, setSubject] = useState('0')
  const [users, setUsers] = useState(null)
  const [usersError, setUsersError] = useState('')
  const [limits, setLimits] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const validSubject = subject !== '' && Number.isSafeInteger(Number(subject)) && Number(subject) >= 0
  const ready = limits?.subject === subject
  useEffect(() => {
    let active = true
    fetchJSON('/api/admin/users').then(result => {
      if (active) setUsers(result.users)
    }).catch(e => { if (active) setUsersError(`账号列表读取失败：${e.message}，请刷新后重试。`) })
    return () => { active = false }
  }, [])
  useEffect(() => {
    let active = true
    setLimits(null); setError(''); setNotice('')
    if (validSubject) {
      fetchJSON(`/api/usage?subject=${Number(subject)}`).then(result => {
        if (!active) return
        const budget = result.budget.user
        setLimits({ subject, day: budget.day.limit == null ? '' : String(budget.day.limit / 1000000), month: budget.month.limit == null ? '' : String(budget.month.limit / 1000000) })
      }).catch(e => { if (active) setError(`额度读取失败：${e.message}，请刷新后重试。`) })
    }
    // 切换对象后忽略旧响应，避免把另一用户的额度回填到当前表单。
    return () => { active = false }
  }, [subject, validSubject])
  const save = async event => {
    event.preventDefault()
    if (!ready || busy) return
    setBusy(true); setError(''); setNotice('')
    try {
      await fetchJSON('/api/usage/limits', { method: 'PUT', body: JSON.stringify({ subject: Number(subject), day: limits.day || null, month: limits.month || null }) })
      setNotice('额度已保存')
      try { await onSaved() } catch { setError('额度已保存，但统计刷新失败，请刷新页面查看。') }
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }
  const edit = (key, value) => { setLimits(current => ({ ...current, [key]: value })); setNotice(''); setError('') }
  return <form onSubmit={save} className="space-y-2">
    <h4 className="text-[12px] font-semibold">配置额度（人民币）</h4>
    <label className="block">预算对象<select name="subject" aria-label="预算对象" value={subject} disabled={busy} onChange={e => setSubject(e.target.value)} className="ml-2 max-w-full border border-border bg-bg p-1">
      <option value="0">0 · 全局</option>
      {users?.map(user => <option key={user.id} value={String(user.id)}>{user.id} · {user.username}{user.role === 'admin' ? '（管理员）' : !user.active ? '（已停用）' : ''}</option>)}
    </select></label>
    {usersError ? <p role="alert" className="text-xs">{usersError}</p> : users === null && <p role="status" className="text-xs">正在读取账号列表…</p>}
    <label className="block">日额度<input name="day" type="number" min="0" step="0.000001" value={ready ? limits.day : ''} disabled={!ready || busy} onChange={e => edit('day', e.target.value)} className="ml-2 border border-border bg-bg p-1" /></label>
    <label className="block">月额度<input name="month" type="number" min="0" step="0.000001" value={ready ? limits.month : ''} disabled={!ready || busy} onChange={e => edit('month', e.target.value)} className="ml-2 border border-border bg-bg p-1" /></label>
    <p className="text-xs">显示当前对象已保存的额度。留空表示未配置，0 表示无云端额度。保存不会清除已用费用。</p>
    <div className="flex flex-wrap items-center gap-3"><button disabled={!ready || busy} className={settingsButton.primary}>保存额度</button>
      {!ready && !error ? <span role="status" className="text-xs text-text-dim">{validSubject ? '正在读取已保存额度…' : '请输入有效的用户编号。'}</span> : <SaveStatus busy={busy} message={notice} error={error} />}
    </div>
  </form>
}

export default function UsagePanel({ admin = false }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [global, setGlobal] = useState(admin)
  const [busy, setBusy] = useState(false)
  const [cloud, setCloud] = useState(null)
  const [notice, setNotice] = useState('')
  const [policy, setPolicy] = useState(null)
  const [action, setAction] = useState('')
  const feedback = target => <SaveStatus busy={busy && action === target} message={action === target ? notice : ''} error={action === target ? error : ''} />
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
    event.preventDefault(); setBusy(true); setError(''); setNotice(''); setAction(path)
    try {
      await fetchJSON(path, { method: path.endsWith('/policy') ? 'PUT' : 'POST', body: JSON.stringify(body) })
      setNotice('已保存核对记录或策略')
      await load(); setCloud(await fetchJSON('/api/usage/cloud-state'))
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }
  return <section className="border-t border-border pt-4 space-y-3" aria-labelledby="usage-title">
    <h3 id="usage-title" className="text-sm font-semibold">{admin ? '用量与预算' : '我的用量'}</h3>
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
          <div className="flex flex-wrap items-center gap-3"><button disabled={busy} className={settingsButton.secondary}>确认核对并结算</button>{feedback(`/api/usage/reconcile/${a.id}`)}</div>
        </form></details>}
      </div>)}
      <RecentCalls key={global ? 'global' : 'personal'} details={data.details} />
      <h4 className="text-[12px] font-semibold">月汇总</h4>
      {data.monthly.map(m => <p key={`${m.month}-${m.model_id}-${m.scene}`} className="text-sm">{m.month} · {m.scene} / {m.model_id} · {m.calls} 次 · {m.input_tokens} 输入 / {m.output_tokens} 输出 Token</p>)}
    </> : <p role="status">正在读取用量…</p>}
    {admin && <BudgetLimits onSaved={load} />}
    {admin && <>
      <p role="status">云端调用：{cloud?.held ? '暂停，等待管理员核对并确认' : '按模型配置和四项预算逐次授权'}</p>
      <form onSubmit={event => {
        const values = Object.fromEntries(new FormData(event.currentTarget))
        manage(event, '/api/usage/cloud-confirmation', { reason: values.reason })
      }} className="space-y-2">
        <label className="block">云端开放／恢复核对依据<input name="reason" required minLength={5} maxLength={500} className="w-full border border-border bg-bg p-1" /></label>
        <p className="text-xs">先核对恢复前后费用及预算，处理全部待核实记录。确认不清空已用费用，模型和个人选择仍须另行配置。</p>
        <div className="flex flex-wrap items-center gap-3"><button disabled={busy} className={settingsButton.primary}>确认费用并允许受控云端调用</button>{feedback('/api/usage/cloud-confirmation')}</div>
      </form>
      {policy && <form onSubmit={event => {
        const values = Object.fromEntries(new FormData(event.currentTarget))
        manage(event, '/api/usage/policy', { detail_days: Number(values.days), warning_threshold: Number(values.threshold), local_concurrency: Number(values.concurrency) })
      }} onChange={() => { if (action === '/api/usage/policy') { setNotice(''); setError('') } }} className="space-y-2">
        <label className="block">调用明细保留天数<input name="days" type="number" required min="1" max="365" defaultValue={policy.detail_days} className="ml-2 border border-border bg-bg p-1" /></label>
        <label className="block">预算预警百分比<input name="threshold" type="number" required min="1" max="99" defaultValue={policy.warning_threshold} className="ml-2 border border-border bg-bg p-1" /></label>
        <label className="block">全局本地推理并发<input name="concurrency" type="number" required min="1" max="8" defaultValue={policy.local_concurrency} className="ml-2 border border-border bg-bg p-1" /></label>
        <div className="flex flex-wrap items-center gap-3"><button disabled={busy} className={settingsButton.primary}>保存用量策略</button>{feedback('/api/usage/policy')}</div>
      </form>}
    </>}
    {!action && error && <p role="alert">{error}</p>}
  </section>
}
