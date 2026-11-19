import { settingsButton } from './settings/buttonStyles'
import { useEffect, useState } from 'react'
import { fetchJSON } from '../hooks/useApi'


export function BackupList({ points, busy, onSelect }) {
  return <ul className="space-y-2">
    {points.map(point => <li key={point.id} className="flex flex-wrap items-center justify-between gap-2 border border-border rounded p-2">
      <div className="min-w-0"><span className="block break-all font-mono text-[11px]">{point.id}</span>
        <span className="text-text-muted text-[11px]">{new Date(point.created_at * 1000).toLocaleString()} · {point.file_count} 个文件</span></div>
      <button className={settingsButton.secondary} disabled={busy} onClick={() => onSelect(point.id)}>选择恢复点</button>
    </li>)}
  </ul>
}

export default function OperationsPanel() {
  const [state, setState] = useState(null)
  const [points, setPoints] = useState([])
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [selected, setSelected] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const load = async () => {
    const [status, list] = await Promise.all([fetchJSON('/api/admin/operations'), fetchJSON('/api/admin/backups')])
    setState(status); setPoints(list.backups)
  }
  useEffect(() => {
    let active = true
    Promise.all([fetchJSON('/api/admin/operations'), fetchJSON('/api/admin/backups')])
      .then(([status, list]) => { if (active) { setState(status); setPoints(list.backups) } })
      .catch(() => { if (active) setMessage('运行状态读取失败，请稍后重试。') })
    return () => { active = false }
  }, [])
  const perform = async (path, body, restoring = false) => {
    setBusy(true); setMessage('')
    try {
      await fetchJSON(path, { method: 'POST', ...(body ? { body: JSON.stringify(body) } : {}) })
      if (restoring) { window.location.reload(); return }
      await load(); setMessage('操作完成')
    } catch (error) { setMessage(error.message || '操作失败，请稍后重试。') }
    finally { setBusy(false) }
  }
  return <section className="mt-4 pt-4 border-t border-border space-y-3" aria-label="备份与运行维护">
    <h3 className="text-[13px] font-medium">备份与运行维护</h3>
    <p className="text-[12px] text-text-muted">备份包含所有账号的数据和私有连接配置，仅在本机受保护目录保存。这里只展示恢复点信息。</p>
    {state && <p className="text-[12px]">最近自动备份：{state.backup_success_at ? new Date(state.backup_success_at * 1000).toLocaleString() : '暂无记录'}
      {state.backup_error && <span className="text-bear"> · 备份失败，请检查本机空间及运行日志</span>}
      <br />云端调用：{state.cloud_hold ? '隔离中' : '按服务策略控制'} · Trace：{state.trace.enabled ? '临时开启，最长 30 分钟' : '关闭'}</p>}
    <div className="flex flex-wrap gap-2">
      <button className={settingsButton.secondary} disabled={busy} onClick={() => perform('/api/admin/backups')}>创建备份</button>
      <button className={settingsButton.secondary} disabled={busy || !state} onClick={() => perform('/api/admin/trace', { enabled: !state?.trace.enabled })}>
        {state?.trace.enabled ? '关闭 Trace' : '临时开启 Trace'}</button>
    </div>
    <BackupList points={points} busy={busy} onSelect={id => { setSelected(id); setConfirmation('') }} />
    {!points.length && <p className="text-[12px] text-text-muted">暂无有效恢复点</p>}
    {selected && <div className="space-y-2 rounded border border-bear/40 p-3">
      <p className="text-[12px] text-bear">恢复会覆盖所有账号的当前数据，并撤销全部登录。系统先创建保护备份，恢复后云端调用仍保持隔离。请输入下方恢复点标识确认。</p>
      <label className="block text-[11px] break-all" htmlFor="restore-confirmation">{selected}</label>
      <input id="restore-confirmation" className="w-full rounded border border-border bg-bg p-2 text-[12px]" autoComplete="off"
        disabled={busy} value={confirmation} onChange={event => setConfirmation(event.target.value)} />
      <div className="flex gap-2"><button className={settingsButton.danger} disabled={busy || confirmation !== selected}
        onClick={() => perform(`/api/admin/backups/${encodeURIComponent(selected)}/restore`, { confirmation }, true)}>确认恢复并重新登录</button>
        <button className={settingsButton.secondary} disabled={busy} onClick={() => setSelected('')}>取消</button></div>
    </div>}
    <p role="status" className="text-[12px]">{busy ? '处理中，请等待完成…' : message}</p>
  </section>
}
