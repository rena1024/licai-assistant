import { useEffect, useState } from 'react'
import { fetchJSON } from '../hooks/useApi'

export const sceneNames = { ask: '问答', briefing: '持仓简报', fundamental: '基本面分析', scanner: '形态审核', multi_lens: '多视角分析', rules: '个人规则', news: '资讯解读', portfolio: '组合复盘', market: '市场分析', sector: '板块解读' }
const capabilities = { text: '文本', tools: '工具', image: '图像', structured: '结构化输出' }
const inputClass = 'w-full rounded border border-border bg-bg px-2 py-2 text-sm'
// 基础地址须与后端适配器追加的协议路径配套；Qwen 默认使用北京地域。
const defaultEndpoints = {
  ollama: 'http://localhost:11434',
  anthropic: 'https://api.anthropic.com',
  openai: 'https://api.openai.com',
  deepseek: 'https://api.deepseek.com',
  qwen: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
  gemini: 'https://generativelanguage.googleapis.com',
  openai_compatible: '',
}

export function ModelChoices({ scene, ids, models, onChange }) {
  return <fieldset className="rounded border border-border p-3 space-y-2">
    <legend>{sceneNames[scene] || scene}</legend>
    {[0, 1, 2].map(index => <label key={index} className="block text-sm">
      {index === 0 ? '主模型' : `备用 ${index}`}
      <select aria-label={`${sceneNames[scene]}${index === 0 ? '主模型' : `备用 ${index}`}`} className={inputClass} value={ids[index] || ''} onChange={e => {
        const next = [...ids]; next[index] = e.target.value
        onChange(next.filter(Boolean))
      }}>
        <option value="">不选择</option>
        {models.map(model => <option key={model.id} value={model.id} disabled={(!model.enabled && !ids.includes(model.id)) || (ids.includes(model.id) && ids[index] !== model.id)}>{model.id} · {model.model} · {model.local ? '本地' : '云端'}{!model.enabled ? '（已停用）' : ''}</option>)}
        {ids[index] && !models.some(m => m.id === ids[index]) && <option value={ids[index]}>{ids[index]}（已失效）</option>}
      </select>
    </label>)}
    {!ids.length && <p className="text-xs text-text-dim">未配置候选，本场景 AI 暂不可用。</p>}
  </fieldset>
}

function ModelServices({ models, reload, execute, busy }) {
  const [editing, setEditing] = useState(null)
  const [protocol, setProtocol] = useState('ollama')
  const [endpoint, setEndpoint] = useState(defaultEndpoints.ollama)
  const [result, setResult] = useState('')
  const [validationError, setValidationError] = useState('')
  const [saving, setSaving] = useState(false)
  const manageModel = async (model, remove = false) => {
    if (remove && !confirm(`删除模型服务 ${model.id}？仅删除本应用配置，不删除 Ollama 模型文件。历史用量保留，用户原选择将显示为失效；此标识不可复用。`)) return
    setResult(''); setValidationError('')
    const response = await execute(`/api/models/${encodeURIComponent(model.id)}`, remove ? 'DELETE' : 'PATCH', {
      expected_version: model.version, ...(remove ? {} : { enabled: !model.enabled }),
    })
    if (!response) return
    if (editing?.id === model.id) {
      setEditing(null); setProtocol('ollama'); setEndpoint(defaultEndpoints.ollama)
    }
    setResult(`模型 ${model.id} 已${remove ? '删除' : model.enabled ? '停用' : '启用'}`)
    try { await reload() }
    catch { setValidationError('操作已完成，但列表刷新失败，请刷新页面查看。') }
  }
  const submit = async event => {
    event.preventDefault()
    setResult(''); setValidationError('')
    const form = event.currentTarget
    const values = Object.fromEntries(new FormData(form))
    const local = protocol === 'ollama'
    const price = local ? null : { ...Object.fromEntries(['input', 'output', 'cache_read', 'cache_write', 'exchange_rate', 'currency'].map(k => [k, values[k]])), cache_write_1h: values.cache_write_1h || null }
    if (protocol === 'deepseek') {
      // 统一计价结构中，cache_read 对应缓存命中输入；DeepSeek 没有独立写入计费。
      price.cache_write = '0'
      price.cache_write_1h = null
    }
    const payload = { id: values.id, protocol, endpoint: values.endpoint.trim().replace(/\/$/, ''), model: values.model,
      local, enabled: values.enabled === 'on', api_key: values.api_key, capabilities: Object.keys(capabilities).filter(c => values[c] === 'on'),
      context_limit: Number(values.context_limit), price, expected_version: editing?.version || 0 }
    setSaving(true)
    try {
      if (await execute('/api/models', 'PUT', payload)) {
        form.reset(); setEditing(null); setProtocol('ollama'); setEndpoint(defaultEndpoints.ollama)
        setResult(`模型 ${payload.id} 已保存`)
        try { await reload() }
        catch { setValidationError('模型已保存，但列表刷新失败，请刷新页面查看。') }
      }
    } finally { setSaving(false) }
  }
  return <section className="space-y-3 border-t border-border pt-3">
    <h4 className="text-[13px] font-semibold">管理：模型服务</h4>
    <p className="text-xs text-text-dim">管理员开放服务，用户自行选择模型。单价按原币 / 百万 Token 填写；价格、汇率或凭证缺失不能开放云端服务。协议能力须实际测试确认。</p>
    <p className="text-xs text-text-dim">停用后可再次启用；删除需先停用，只移除本应用服务配置，历史用量保留。</p>
    {models.map(m => <div key={m.id} className="rounded border border-border p-2 text-sm">
      <p>{m.id} · {m.model} · v{m.version} · {m.local ? '本地' : '云端'} · {m.enabled ? '已开放' : '已停用'}</p>
      <p>{m.capabilities.map(c => capabilities[c]).join(' / ')} · {m.validation_status || '尚未真实联调'}</p>
      <button type="button" disabled={busy} onClick={() => { setEditing(m); setProtocol(m.protocol); setEndpoint(m.endpoint) }} className="mr-3 text-accent">编辑 {m.id}</button>
      <button type="button" disabled={busy || saving} aria-label={`${m.enabled ? '停用' : '启用'} ${m.id}`} onClick={() => manageModel(m)} className="mr-3 text-accent disabled:opacity-50">{m.enabled ? '停用' : '启用'}</button>
      <button type="button" disabled={busy || saving || m.enabled} aria-label={`删除 ${m.id}`} title={m.enabled ? '请先停用，再删除' : '删除模型服务配置'} onClick={() => manageModel(m, true)} className="mr-3 text-bear disabled:opacity-50">删除</button>
      {m.capabilities.map(capability => <button key={capability} type="button" disabled={busy} onClick={async () => {
        if (!confirm(`将对 ${m.id} 发送测试请求，云端测试可能产生费用并占用全局预算。继续？`)) return
        const response = await execute(`/api/models/${encodeURIComponent(m.id)}/test`, 'POST', { capability })
        if (response) { setResult(`${m.id} ${capabilities[capability]}测试通过`); await reload() }
      }} className="mr-3 text-accent">测试{capabilities[capability]}</button>)}
    </div>)}
    <form key={editing ? `${editing.id}-${editing.version}` : 'new'} onSubmit={submit} onInvalid={event => {
      setResult('')
      const field = event.target
      // 原生校验会阻止 submit，需在保存按钮旁说明未发起请求的原因。
      const firstInvalid = event.currentTarget.querySelector(':invalid')
      if (field === firstInvalid) setValidationError(`${field.labels?.[0]?.textContent || '模型配置'}：${field.validationMessage}`)
    }} className="grid gap-3 sm:grid-cols-2">
      <h5 className="sm:col-span-2 text-[12px] font-medium">{editing ? `编辑 ${editing.id}` : '新增模型'}</h5>
      <label>模型标识<input name="id" required pattern="[a-zA-Z0-9_\-]{1,64}" title="请输入 1–64 位英文字母、数字、下划线或连字符，例如 local-qwen" aria-describedby="model-id-help" readOnly={!!editing} defaultValue={editing?.id || ''} className={inputClass} /></label>
      <label>协议<select value={protocol} onChange={e => {
        setProtocol(e.target.value); setEndpoint(defaultEndpoints[e.target.value])
      }} className={inputClass}>
        {Object.keys(defaultEndpoints).map(p => <option key={p}>{p}</option>)}
      </select></label>
      <label>服务基础地址<input name="endpoint" type="url" required value={endpoint} onChange={e => setEndpoint(e.target.value)} aria-describedby="model-endpoint-help" placeholder={protocol === 'openai_compatible' ? 'https://example.com/v1' : undefined} className={inputClass} /></label>
      <label>厂商模型 ID<input name="model" required defaultValue={editing?.model || ''} className={inputClass} /></label>
      <p id="model-endpoint-help" className="sm:col-span-2 text-xs text-text-dim">切换协议会填入默认基础地址，仍可手动修改。{protocol === 'qwen' ? 'Qwen 默认北京地域，其他地域请按账号修改地址。' : protocol === 'openai_compatible' ? '自定义兼容服务请填写服务商提供的基础地址。' : '请求路径由系统自动追加。'}</p>
      <p id="model-id-help" className="sm:col-span-2 text-xs text-text-dim">模型标识是自定义名称，仅支持 1–64 位英文字母、数字、下划线或连字符，例如 local-qwen；qwen3:4b 等实际名称请填写在“厂商模型 ID”。</p>
      <label>上下文 Token 上限<input name="context_limit" type="number" min="1024" max="2000000" required defaultValue={editing?.context_limit || 32768} className={inputClass} /></label>
      <label>API Key（留空保留）<input name="api_key" type="password" autoComplete="new-password" className={inputClass} /></label>
      {protocol !== 'ollama' && <>
        <label>原币<select name="currency" defaultValue={editing?.price?.currency || 'CNY'} className={inputClass}><option>CNY</option><option>USD</option></select></label>
        {(protocol === 'deepseek' ? ['exchange_rate', 'cache_read', 'input', 'output'] : ['exchange_rate', 'input', 'output', 'cache_read', 'cache_write']).map(k => <label key={k}>{({ exchange_rate: '人民币汇率', input: protocol === 'deepseek' ? '缓存未命中输入单价' : '输入单价', output: '输出单价', cache_read: protocol === 'deepseek' ? '缓存命中输入单价' : '缓存读取单价', cache_write: '缓存写入单价' })[k]}<input name={k} required type="number" min={k === 'exchange_rate' ? '0.000001' : '0'} step="any" defaultValue={editing?.price?.[k] ?? ''} className={inputClass} /></label>)}
        {protocol !== 'deepseek' && <label>一小时缓存写入单价（可选）<input name="cache_write_1h" type="number" min="0" step="any" defaultValue={editing?.price?.cache_write_1h ?? ''} className={inputClass} /></label>}
      </>}
      <fieldset className="sm:col-span-2"><legend>开放能力</legend>{Object.entries(capabilities).map(([key, label]) => <label key={key} className="mr-4"><input type="checkbox" name={key} defaultChecked={editing ? editing.capabilities.includes(key) : key === 'text'} /> {label}</label>)}</fieldset>
      <label><input type="checkbox" name="enabled" defaultChecked={editing ? editing.enabled : true} /> 开放此模型</label>
      <div><button type="submit" disabled={busy || saving} className="rounded bg-accent px-3 py-2 text-bg">{saving ? '正在保存模型…' : '保存模型'}</button>{editing && <button type="button" disabled={busy || saving} onClick={() => { setEditing(null); setProtocol('ollama'); setEndpoint(defaultEndpoints.ollama) }} className="ml-3">取消编辑</button>}</div>
    </form>
    {result && <p role="status">{result}</p>}
    {validationError && <p role="alert">{validationError}</p>}
  </section>
}

export default function ModelSettings({ admin }) {
  const [models, setModels] = useState([])
  const [prefs, setPrefs] = useState(null)
  const [error, setError] = useState('')
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(false)
  const reload = async () => setModels((await fetchJSON('/api/models')).models)
  useEffect(() => {
    let active = true
    Promise.all([fetchJSON('/api/models'), fetchJSON('/api/models/preferences')]).then(([catalog, personal]) => {
      if (active) { setModels(catalog.models); setPrefs(personal) }
    }).catch(e => { if (active) setError(e.message) })
    return () => { active = false }
  }, [])
  const execute = async (path, method, body) => {
    setBusy(true); setError(''); setStatus('')
    try { return await fetchJSON(path, { method, body: JSON.stringify(body) }) }
    catch (e) { setError(e.message); return null }
    finally { setBusy(false) }
  }
  return <section aria-labelledby="model-settings-title" className="border-t border-border pt-4 space-y-3">
    <h3 id="model-settings-title" className="text-sm font-semibold">我的 AI 设置</h3>
    <p className="text-xs text-text-dim">只按你选择的主模型和备用顺序调用。仅本地模式仍可使用在线行情和资讯；无合格本地模型时 AI 暂不可用，手工理财功能照常使用。</p>
    {prefs ? <>
      <label className="block"><input type="checkbox" checked={prefs.local_only} onChange={e => setPrefs({ ...prefs, local_only: e.target.checked })} /> 仅本地推理（保存后阻止旧任务后续云端请求）</label>
      <label className="block"><input type="checkbox" checked={prefs.automatic_ai_enabled} onChange={e => setPrefs({ ...prefs, automatic_ai_enabled: e.target.checked })} /> 启用本人自动 AI 任务（可能产生费用，错过执行窗口不补跑）</label>
      <div className="grid gap-3 sm:grid-cols-2">{Object.keys(sceneNames).map(scene => <ModelChoices key={scene} scene={scene} ids={prefs.scenes[scene] || []} models={models} onChange={ids => setPrefs({ ...prefs, scenes: { ...prefs.scenes, [scene]: ids } })} />)}</div>
      <button disabled={busy} className="rounded bg-accent px-3 py-2 text-bg" onClick={async () => {
        const saved = await execute('/api/models/preferences', 'PUT', { local_only: prefs.local_only, scenes: prefs.scenes, automatic_ai_enabled: prefs.automatic_ai_enabled, expected_version: prefs.version })
        if (saved) { setPrefs(saved); setStatus('个人 AI 设置已保存') }
      }}>保存我的设置</button>
    </> : <p role="status">正在读取 AI 设置…</p>}
    {admin && <ModelServices models={models} reload={reload} execute={execute} busy={busy} />}
    {status && <p role="status">{status}</p>}{error && <p role="alert">{error}</p>}
  </section>
}
