import { useEffect, useState } from 'react'
import { fetchJSON } from '../hooks/useApi'

export function UserList({ users, busy, onActive, onReset }) {
  return <ul className="divide-y divide-border">
    {users.map(user => <li key={user.id} className="py-3 flex flex-wrap items-center gap-3">
      <span className="flex-1 text-sm">{user.username} · {user.role === 'admin' ? '唯一管理员' : user.active ? '已启用' : '已停用'}</span>
      {user.role !== 'admin' && <>
        <button type="button" disabled={busy} onClick={() => onReset(user)} className="text-sm text-accent">重置密码<span className="sr-only"> {user.username}</span></button>
        <button type="button" disabled={busy} aria-label={`${user.active ? '停用' : '启用'} ${user.username}`} onClick={() => onActive(user)} className="text-sm text-accent">{user.active ? '停用' : '启用'}</button>
      </>}
    </li>)}
  </ul>
}

export default function AdminUsers() {
  const [users, setUsers] = useState([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [reset, setReset] = useState(null)
  const load = async () => setUsers((await fetchJSON('/api/admin/users')).users)
  useEffect(() => {
    let active = true
    fetchJSON('/api/admin/users').then(data => { if (active) setUsers(data.users) }).catch(err => { if (active) setError(err.message) })
    return () => { active = false }
  }, [])
  const execute = async (url, method, values) => {
    setBusy(true); setError('')
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
  return <section className="mt-4 border-t border-border pt-4" aria-labelledby="admin-users-title">
    <h2 id="admin-users-title" className="font-semibold">账号管理</h2>
    <p className="my-2 text-xs text-text-dim">停用保留数据并停止后续任务；重新启用不补跑。临时密码由使用者首次登录后修改。</p>
    <UserList users={users} busy={busy} onReset={setReset} onActive={user => execute(`/api/admin/users/${user.id}/active`, 'PUT', { active: !user.active })} />
    <form onSubmit={submit} className="mt-3 space-y-3">
      <h3 className="text-sm">{reset ? `重置 ${reset.username} 的临时密码` : '创建普通账号'}</h3>
      {!reset && <label className="block text-sm">账号名<input name="username" required minLength={3} maxLength={64} autoComplete="off" className="w-full rounded border border-border bg-surface-2 px-3 py-2" /></label>}
      <label className="block text-sm">临时密码<input key={reset?.id || 'new'} name="password" type="password" required minLength={4} maxLength={128} autoComplete="new-password" className="w-full rounded border border-border bg-surface-2 px-3 py-2" /></label>
      <button disabled={busy} className="rounded bg-accent px-3 py-2 text-bg">{busy ? '处理中…' : reset ? '重置临时密码' : '创建账号'}</button>
      {reset && <button type="button" disabled={busy} onClick={() => setReset(null)} className="ml-3 text-sm">取消</button>}
    </form>
    {error && <p role="alert" className="mt-2 text-sm">{error}</p>}
  </section>
}
