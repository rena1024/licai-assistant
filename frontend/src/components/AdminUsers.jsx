import { settingsButton } from './settings/buttonStyles'
import { useEffect, useState } from 'react'
import { fetchJSON } from '../hooks/useApi'

export function UserList({ users, busy, onActive, onReset, onDelete }) {
  return <ul className="divide-y divide-border">
    {users.map(user => <li key={user.id} className="py-3 flex flex-wrap items-center gap-3">
      <span className="flex-1 text-sm">{user.username} · {user.role === 'admin' ? '唯一管理员' : user.active ? '已启用' : '已停用'}</span>
      {user.role !== 'admin' && <>
        <button type="button" disabled={busy} aria-label={`重置密码 ${user.username}`} onClick={() => onReset(user)} className={settingsButton.secondary}>重置密码</button>
        <button type="button" disabled={busy} aria-label={`${user.active ? '停用' : '启用'} ${user.username}`} onClick={() => onActive(user)} className={settingsButton.secondary}>{user.active ? '停用' : '启用'}</button>
        <button type="button" disabled={busy || user.active} title={user.active ? '请先停用账号' : '删除账号及其个人数据'} aria-label={`删除 ${user.username}`} onClick={() => onDelete(user)} className={settingsButton.danger}>删除</button>
      </>}
    </li>)}
  </ul>
}

export default function AdminUsers() {
  const [users, setUsers] = useState([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [reset, setReset] = useState(null)
  const [deleting, setDeleting] = useState(null)
  const [confirmation, setConfirmation] = useState('')
  const [notice, setNotice] = useState('')
  const [pending, setPending] = useState([])
  const load = async () => {
    const data = await fetchJSON('/api/admin/users')
    setUsers(data.users); setPending(data.pending_deletions || [])
  }
  useEffect(() => {
    let active = true
    fetchJSON('/api/admin/users').then(data => { if (active) { setUsers(data.users); setPending(data.pending_deletions || []) } }).catch(err => { if (active) setError(err.message) })
    return () => { active = false }
  }, [])
  const execute = async (url, method, values) => {
    setBusy(true); setError(''); setNotice('')
    try {
      await fetchJSON(url, { method, body: JSON.stringify(values) })
      await load()
      return true
    } catch (err) { setError(err.message); return false }
    finally { setBusy(false) }
  }
  const submit = async event => {
    event.preventDefault()
    const form = event.currentTarget
    const data = Object.fromEntries(new FormData(form))
    const url = reset ? `/api/admin/users/${reset.id}/password` : '/api/admin/users'
    if (await execute(url, 'POST', data)) { form.reset(); setReset(null) }
  }
  const remove = async event => {
    event.preventDefault()
    if (!deleting || confirmation !== deleting.username || busy) return
    setBusy(true); setError(''); setNotice('')
    try {
      const result = await fetchJSON(`/api/admin/users/${deleting.id}`, { method: 'DELETE', body: JSON.stringify({ confirmation_username: confirmation }) })
      const deletedId = deleting.id
      setUsers(current => current.filter(user => user.id !== deletedId))
      if (result.files_pending) setPending(current => [...current.filter(item => item.id !== deletedId), { id: deletedId }])
      setDeleting(null); setConfirmation('')
      setNotice(result.files_pending ? '账号与数据库中的个人数据已删除，文件清理未完成，请重试清理。' : '账号及个人数据已删除，历史费用与审计记录已保留。')
      try { await load() } catch { setError('删除已完成，但账号列表刷新失败，请刷新页面查看。') }
    } catch (err) { setError(err.message) } finally { setBusy(false) }
  }
  const retry = async id => {
    setBusy(true); setError(''); setNotice('')
    try {
      const result = await fetchJSON(`/api/admin/users/${id}/deletion-cleanup`, { method: 'POST' })
      setNotice(result.files_pending ? '文件清理仍未完成，请检查运行目录权限后重试。' : '个人文件已清理。')
      await load()
    } catch (err) { setError(err.message) } finally { setBusy(false) }
  }
  return <section className="mt-4 border-t border-border pt-4" aria-labelledby="admin-users-title">
    <h3 id="admin-users-title" className="text-sm font-semibold">账号管理</h3>
    <p className="my-2 text-xs text-text-dim">停用保留数据并停止后续任务；重新启用不补跑。仅已停用的普通账号可删除。临时密码由使用者首次登录后修改。</p>
    <UserList users={users} busy={busy} onReset={user => { setDeleting(null); setReset(user); setError(''); setNotice('') }} onActive={user => execute(`/api/admin/users/${user.id}/active`, 'PUT', { active: !user.active })} onDelete={user => { setReset(null); setDeleting(user); setConfirmation(''); setError(''); setNotice('') }} />
    {deleting && <form onSubmit={remove} className="my-3 space-y-3 rounded border border-bear/40 p-3" aria-label="删除用户确认">
      <h4 className="text-sm font-semibold">删除 {deleting.username}</h4>
      <p className="text-xs text-text-dim">将清除该账号的持仓、交易、问答、提醒、通知配置、个人凭证及图片，无法在此撤销。费用账本与审计保留匿名身份关联。</p>
      <p className="text-xs text-text-dim">已有备份不会改写；恢复旧备份可能恢复该账号及旧数据。</p>
      <label className="block text-sm">输入用户名确认删除<input value={confirmation} disabled={busy} onChange={event => setConfirmation(event.target.value)} autoComplete="off" required className="w-full rounded border border-border bg-bg px-3 py-2" /></label>
      <div className="flex flex-wrap gap-3"><button disabled={busy || confirmation !== deleting.username} className={settingsButton.danger}>永久删除账号与个人数据</button><button type="button" disabled={busy} onClick={() => setDeleting(null)} className={settingsButton.secondary}>取消删除</button></div>
    </form>}
    {pending.map(item => <div key={item.id} className="my-2 flex flex-wrap items-center gap-3 text-sm"><span>已删除账号（编号 {item.id}）：个人文件待清理</span><button type="button" disabled={busy} onClick={() => retry(item.id)} className={settingsButton.secondary}>重试清理</button></div>)}
    <form onSubmit={submit} className="mt-3 space-y-3">
      <h4 className="text-[12px] font-semibold">{reset ? `重置 ${reset.username} 的临时密码` : '创建普通账号'}</h4>
      {!reset && <label className="block text-sm">账号名<input name="username" required minLength={3} maxLength={64} autoComplete="off" className="w-full rounded border border-border bg-surface-2 px-3 py-2" /></label>}
      <label className="block text-sm">临时密码<input key={reset?.id || 'new'} name="password" type="password" required minLength={4} maxLength={128} autoComplete="new-password" className="w-full rounded border border-border bg-surface-2 px-3 py-2" /></label>
      <button disabled={busy} className={settingsButton.primary}>{busy ? '处理中…' : reset ? '重置临时密码' : '创建账号'}</button>
      {reset && <button type="button" disabled={busy} onClick={() => setReset(null)} className={`ml-3 ${settingsButton.secondary}`}>取消</button>}
    </form>
    {error && <p role="alert" className="mt-2 text-sm">{error}</p>}
    {notice && <p role="status" className="mt-2 text-sm">{notice}</p>}
  </section>
}
