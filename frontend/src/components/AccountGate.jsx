import { useEffect, useState } from 'react'
import { fetchJSON } from '../hooks/useApi'
import { apiFetch, invalidateSession, SESSION_ENDED, setSessionToken } from '../hooks/sessionRuntime'

// 身份变化后重载文档，确保模块级问答缓存、表单与页面状态全部重新创建。
function reloadAccount() {
  invalidateSession()
  try { localStorage.setItem('licai:account-changed', String(Date.now())) } catch {}
  window.location.replace(window.location.pathname)
}

export function AccountForm({ mode, onSuccess, onCancel, outerError, mustChange, currentUsername }) {
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [password, setPassword] = useState('')
  const switching = mode === 'switch'
  const change = mode === 'password'
  const bootstrap = mode === 'bootstrap'
  const title = change ? '修改密码' : bootstrap ? '创建管理员' : switching ? '切换账号' : '登录理财助手'
  const submit = async (event) => {
    event.preventDefault()
    const form = event.currentTarget
    const values = Object.fromEntries(new FormData(form))
    if (change && values.new_password !== values.confirm_password) {
      setError('两次输入的新密码不一致')
      return
    }
    delete values.confirm_password
    setBusy(true)
    setError('')
    try {
      await fetchJSON(`/api/auth/${change ? 'password' : bootstrap ? 'bootstrap' : 'login'}`, { method: 'POST', body: JSON.stringify(values) })
      form.reset()
      onSuccess()
    } catch (err) { setError(err.message) }
    finally { setBusy(false) }
  }
  const inputClass = 'w-full rounded-lg border border-border bg-surface-2 px-3 py-2 text-text-bright'
  return (
    <div className="min-h-screen flex items-center justify-center bg-bg px-4 py-8">
      <section className="w-full max-w-sm rounded-xl border border-border bg-surface p-6">
        <h1 className="text-xl font-semibold text-text-bright mb-3">{title}</h1>
        <p className="text-sm text-text-dim mb-5">{change ? '修改后所有设备的旧会话失效，请重新登录。' : bootstrap ? '请使用部署机器上生成的一次性初始化凭据。' : switching ? `当前账号 ${currentUsername} 仍保持登录；新账号登录成功后才切换。` : '使用管理员创建的账号登录。会话固定保留 30 天。'}</p>
        <form onSubmit={submit} className="space-y-4">
          {bootstrap && <label className="block text-sm text-text">初始化凭据<input name="token" type="password" autoComplete="off" required maxLength={128} className={inputClass} /></label>}
          {!change && <label className="block text-sm text-text">账号<input name="username" autoComplete="username" required maxLength={64} className={inputClass} /></label>}
          <label className="block text-sm text-text">{change ? '当前密码' : '密码'}<input name={change ? 'current_password' : 'password'} type="password" autoComplete={bootstrap ? 'new-password' : 'current-password'} required minLength={4} onChange={change ? undefined : event => setPassword(event.target.value)} aria-describedby="password-policy" maxLength={128} className={inputClass} /></label>
          {change && <>
            <label className="block text-sm text-text">新密码<input name="new_password" onChange={event => setPassword(event.target.value)} aria-describedby="password-policy" type="password" autoComplete="new-password" required minLength={4} maxLength={128} className={inputClass} /></label>
            <label className="block text-sm text-text">确认新密码<input name="confirm_password" type="password" autoComplete="new-password" required minLength={4} maxLength={128} className={inputClass} /></label>
          </>}
          <PasswordHint password={password} />
          {(error || outerError) && <p role="alert" className="text-sm text-text">{error || outerError}</p>}
          <button disabled={busy} className="w-full rounded-lg bg-accent text-bg px-4 py-2 disabled:opacity-50">{busy ? '处理中…' : title}</button>
          {onCancel && <button type="button" disabled={busy} onClick={onCancel} className="w-full text-sm text-text-dim">{switching ? '取消切换' : mustChange ? '退出登录' : '返回'}</button>}
        </form>
      </section>
    </div>
  )
}

export function PasswordHint({ password }) {
  return <div id="password-policy" className="text-xs text-text-dim">
    <p>密码长度为 4～128 个字符。</p>
    <p role="status" aria-live="polite" className="text-accent">{password.length > 0 && password.length < 8 ? '密码强度太弱，建议至少使用 8 个字符；仍可继续。' : ''}</p>
  </div>
}

export default function AccountGate({ children }) {
  const [state, setState] = useState({ mode: 'loading' })
  const [switching, setSwitching] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => {
    let active = true
    const ended = () => { if (active) { setSwitching(false); setState({ mode: 'login' }) } }
    const changed = (event) => {
      if (event.key === 'licai:account-changed') { invalidateSession(); window.location.replace(window.location.pathname) }
    }
    window.addEventListener(SESSION_ENDED, ended)
    window.addEventListener('storage', changed)
    const load = async () => {
      try {
        const status = await fetchJSON('/api/auth/bootstrap/status')
        if (!active) return
        if (!status.initialized) { setState({ mode: 'bootstrap' }); return }
        const response = await apiFetch('/api/auth/me', { cache: 'no-store', credentials: 'same-origin' })
        if (!active) return
        if (response.status === 401) { setState({ mode: 'login' }); return }
        if (!response.ok) throw new Error('无法检查登录状态，请刷新后重试')
        const session = await response.json()
        if (!active) return
        setSessionToken(session.csrf_token)
        setState({ mode: session.account.must_change_password ? 'password' : 'ready', account: session.account })
      } catch (err) { if (active) { setError(err.message); setState({ mode: 'error' }) } }
    }
    load()
    return () => {
      active = false
      window.removeEventListener(SESSION_ENDED, ended)
      window.removeEventListener('storage', changed)
    }
  }, [])
  const logout = async () => {
    try { await fetchJSON('/api/auth/logout', { method: 'POST' }); reloadAccount() }
    catch (err) { setError(err.message) }
  }
  if (state.mode === 'loading') return <p role="status" className="p-6 text-text-dim">正在检查登录状态…</p>
  if (state.mode === 'error') return <p role="alert" className="p-6 text-text">{error}</p>
  if (state.mode !== 'ready') return <AccountForm key={state.mode} mode={state.mode} outerError={error} mustChange={state.account?.must_change_password} onSuccess={reloadAccount} onCancel={state.mode === 'password' ? (state.account?.must_change_password ? logout : reloadAccount) : undefined} />
  return <>
    <div className={switching ? 'hidden' : 'h-full flex flex-col'} inert={switching}>
    <div className="flex flex-wrap items-center justify-end gap-3 border-b border-border bg-surface px-4 py-1 text-xs text-text-dim">
      <span>当前账号：{state.account.username}</span>
      <button onClick={() => setState({ ...state, mode: 'password' })}>修改密码</button>
      <button onClick={() => { setError(''); setSwitching(true) }}>切换账号</button>
      <button onClick={logout}>退出登录</button>
      {error && <span role="alert">{error}</span>}
    </div>
    <div className="flex-1 min-h-0">{typeof children === 'function' ? children(state.account) : children}</div>
    </div>
    {switching && <div className="fixed inset-0 z-[100] overflow-y-auto bg-bg">
      <AccountForm mode="switch" currentUsername={state.account.username} onSuccess={reloadAccount} onCancel={() => setSwitching(false)} />
    </div>}
  </>
}
