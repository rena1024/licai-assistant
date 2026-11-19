import SaveStatus from './SaveStatus'
import { settingsButton } from './buttonStyles'
import { useEffect, useState } from 'react'
import { fetchJSON } from '../../hooks/useApi'

/* 扩展数据源(provider, 可选)。

   本项目自带的行情源都是公开接口、不要凭证。少数口径(带身份标签的深度龙虎榜、集合竞价异动、
   机构季度增减仓)只有商业终端才有 —— 那要用你自己的账号凭证去打对方的接口, 是**你自己**与
   对方的关系, 所以项目只定义协议、不自带任何实现。装哪个、填不填, 由你在这里按一次开关。

   表单是**通用的**: 要填哪几个字段由 provider 自己声明(后端 /api/settings/provider 的 fields),
   这里照着渲染 —— 换一个 provider 不用改这个组件。secret 字段只存不回显。 */
export function ProviderSection({ admin = false, managementOnly = false }) {
  const [st, setSt] = useState(null)
  const [spec, setSpec] = useState('')
  const [vals, setVals] = useState({})
  const [busy, setBusy] = useState('')
  const [action, setAction] = useState('')
  const [msg, setMsg] = useState({ text: '', ok: null })

  const load = () => fetchJSON('/api/settings/provider').then(r => { setSt(r); setSpec(r?.spec || '') }).catch(() => {})
  useEffect(() => { load() }, [])

  const post = async (body, tag) => {
    setAction(tag); setBusy(tag); setMsg({ text: '', ok: null })
    try {
      const r = await fetchJSON('/api/settings/provider', { method: 'POST', body: JSON.stringify(body) })
      setSt(r); setSpec(r?.spec || '')
      if (body.values) setVals({})
      setMsg({ text: r.note || (r.ok ? '已保存' : '保存了但探活未通过'), ok: r.ok === false ? false : (r.valid ?? null) })
    } catch (e) { setMsg({ text: String(e?.message || e), ok: false }) } finally { setBusy('') }
  }

  const clear = async () => {
    setAction('clear'); setBusy('clear')
    try {
      const r = await fetchJSON(`/api/settings/provider${admin ? '' : '?credentials_only=true'}`, { method: 'DELETE' })
      setSt(r); setSpec(''); setVals({}); setMsg({ text: admin ? '已卸载' : '本人凭证已清除', ok: null })
    } catch (e) { setMsg({ text: String(e?.message || e), ok: false }) } finally { setBusy('') }
  }

  const fields = st?.fields || []
  const installed = !!st?.spec
  // 已装但还没选中的候选。列出来 ≠ 启用 —— 点一下只是把名字填进输入框, 还得按「装载」。
  const candidates = (st?.discovered || []).filter(d => d.spec !== st?.spec && d.name !== st?.spec)
  const canSaveCreds = fields.length > 0 && fields.every(f => (vals[f.key] || '').trim())

  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
        <label className="text-[12px] text-text-dim font-semibold">扩展数据源（可选 · 深度龙虎榜 / 竞价异动 / 机构增仓）</label>
        <span className="shrink-0 text-[11px] font-mono text-text-muted">
          {!st ? '' : st.load_error ? '加载失败' : !installed ? '未接入'
            : !fields.length ? st.display_name
            : st.valid ? `${st.display_name} · 已就绪` : st.configured ? '凭证失效' : '待填凭证'}
        </span>
      </div>

      <p className="text-[11px] text-text-muted mb-2 leading-relaxed">
        自带的行情源（东财 / 新浪 / 腾讯 / Yahoo）都是公开接口、不要凭证，缺的是深度龙虎榜席位身份、集合竞价异动、机构季度增减仓这几个口径。
        这些由<span className="text-[var(--color-signal-moderate)]">你自己安装的 provider 插件</span>提供，项目本身不内置任何实现，也不内置任何账号。
        不填这里，其余功能一切照常。
      </p>

      {/* provider 入口: "包名:类名"。留空 = 不接入 */}
      {admin && <div className="flex flex-wrap items-center gap-2 mb-2">
        <input className="w-full min-w-0 sm:w-auto sm:flex-1 bg-bg border border-border rounded px-3 py-1.5 text-[12px] text-text font-mono outline-none focus:border-accent"
          placeholder="provider 入口，如 mypkg.provider:Provider（留空=不接入）"
          value={spec} onChange={e => { setSpec(e.target.value); setMsg({ text: '', ok: null }) }} autoComplete="off" spellCheck={false} />
        <button onClick={() => post({ spec }, 'spec')} disabled={!!busy || spec === (st?.spec || '')}
          className={settingsButton.primary}>
          {busy === 'spec' ? '加载中' : '装载'}
        </button>
        <SaveStatus busy={busy === 'spec'} message={action === 'spec' && msg.ok !== false ? msg.text : ''} error={action === 'spec' && msg.ok === false ? msg.text : ''} />
        {installed && (
          <button onClick={clear} disabled={!!busy}
            className={settingsButton.danger}>卸载</button>
        )}
      </div>}

      {admin && !!candidates.length && (
        <div className="flex items-baseline gap-2 flex-wrap mb-2">
          <span className="text-[11px] text-text-muted shrink-0">已装可选：</span>
          {candidates.map(d => (
            <button key={d.spec} onClick={() => setSpec(d.name)} disabled={!!busy}
              title={`${d.spec}${d.dist ? ` · ${d.dist} ${d.version}` : ''}`}
              className={settingsButton.secondary}>
              {d.name}{d.version && <span className="text-text-muted ml-1">{d.version}</span>}
            </button>
          ))}
        </div>
      )}

      {!managementOnly && !admin && installed && fields.length > 0 && <button type="button" onClick={clear} disabled={!!busy} className={`mb-2 ${settingsButton.danger}`}>清除本人凭证</button>}

      {st?.load_error && <div className="text-[11px] text-bear mb-2 break-all">{st.load_error}</div>}

      {/* 凭证表单: 字段由 provider 声明, 这里不写死任何一个 provider 的表单 */}
      {!managementOnly && installed && !st?.load_error && fields.length > 0 && (
        <>
          <div className="flex flex-wrap items-center gap-2 mb-2 flex-wrap">
            {fields.map(f => (
              <input key={f.key}
                className={`${f.echo ? 'w-32' : 'flex-1 min-w-[160px]'} bg-bg border border-border rounded px-3 py-1.5 text-[12px] text-text font-mono outline-none focus:border-accent`}
                type={f.secret ? 'password' : 'text'}
                placeholder={f.placeholder || f.label}
                value={vals[f.key] || ''}
                onChange={e => { setVals(v => ({ ...v, [f.key]: e.target.value })); setMsg({ text: '', ok: null }) }}
                autoComplete="off" spellCheck={false} />
            ))}
            <button onClick={() => post({ values: vals }, 'creds')} disabled={!!busy || !canSaveCreds}
              className={settingsButton.primary}>
              {busy === 'creds' ? '验证中' : '保存并验证'}
            </button>
            <SaveStatus busy={busy === 'creds'} message={action === 'creds' && msg.ok !== false ? msg.text : ''} error={action === 'creds' && msg.ok === false ? msg.text : ''} />
          </div>
          <p className="text-[11px] text-text-muted mb-2 leading-relaxed">
            凭证存本机数据库、不进代码库；标了 secret 的字段界面不回显明文。
            {Object.entries(st?.echo || {}).filter(([, v]) => v).map(([k, v]) => (
              <span key={k} className="ml-2 font-mono text-text-dim">{k}: {v}</span>
            ))}
          </p>
        </>
      )}

      {installed && !st?.load_error && !!(st?.capabilities || []).length && (
        <div className="text-[11px] text-text-muted mb-1">
          已启用能力：<span className="font-mono text-text-dim">{st.capabilities.join(' · ')}</span>
        </div>
      )}

      {action === 'clear' && msg.text && (
        <span className={`text-[12px] font-medium break-all ${msg.ok === true ? 'text-bull' : msg.ok === false ? 'text-bear' : 'text-text-dim'}`}>
          {msg.text}
        </span>
      )}
    </>
  )
}
