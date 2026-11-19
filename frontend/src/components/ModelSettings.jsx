import { settingsButton } from './settings/buttonStyles'
import SaveStatus from './settings/SaveStatus'
import { useEffect, useState } from 'react'
import { fetchJSON } from '../hooks/useApi'
import useLLMStatus from '../hooks/useLLMStatus'

export const sceneNames = { ask: '问答', briefing: '持仓简报', fundamental: '基本面分析', scanner: '形态审核', multi_lens: '多视角分析', rules: '个人规则', news: '资讯解读', portfolio: '组合复盘', market: '市场分析', sector: '板块解读' }
const sceneDescriptions = {
  ask: '问问市场；持仓、榜单等位置的个股“问 AI”。需要工具调用能力，发送图片时还需要图像能力。',
  briefing: '市场 → 开盘·情绪 → 早盘简报：生成持仓摘要和风险提示。',
  fundamental: '早盘简报、问答风险检查中的基本面健康度评估，含新闻与公告情绪评分；没有独立页面。',
  scanner: '市场 → 榜单 → 蓄势/强势：对候选 K 线进行看图复核。需要图像能力。',
  multi_lens: '个股“问 AI”弹窗 → 深挖：多视角分析与综合结论。',
  rules: '设置 → AI 读盘规则 → 立刻挖一次：从历史纠正中起草个人规则，批准后生效。',
  news: '市场 → 资讯 → LLM 摘要、单条资讯解读；我的 → 复盘 → 今日组合归因。',
  portfolio: '我的 → 复盘 → 今日、本周、本月、总览：交易纪律与组合复盘；今日组合归因使用“资讯解读”。',
  market: '市场 → 开盘·情绪 → AI 情绪解读：分析情绪周期、量能与热点。',
  sector: '市场 → 板块 → AI 趋势分析、板块异动解读。',
}
const capabilities = { text: '文本', tools: '工具', image: '图像', structured: '结构化输出' }
const inputClass = 'w-full rounded border border-border bg-bg px-2 py-2 text-sm'
const savedKeyMask = '********'
const preferencesPath = '/api/models/preferences'
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

export function ModelChoices({ scene, ids, models, onChange, disabled, children }) {
  return <fieldset disabled={disabled} className="rounded border border-border p-3 space-y-2" aria-describedby={`scene-${scene}-description`}>
    <legend>{sceneNames[scene] || scene}</legend>
    <p id={`scene-${scene}-description`} className="text-xs text-text-dim leading-relaxed break-words">对应功能：{sceneDescriptions[scene] || '按本场景的模型选择执行。'}</p>
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
    {children}
  </fieldset>
}

function ModelServices({ models, reload, execute, busy, error, showTitle = true }) {
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
      local, enabled: values.enabled === 'on', api_key: editing?.has_key && values.api_key === savedKeyMask ? '' : values.api_key, capabilities: Object.keys(capabilities).filter(c => values[c] === 'on'),
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
    {showTitle && <h4 className="text-[13px] font-semibold">管理：模型服务</h4>}
    <p className="text-xs text-text-dim">管理员开放服务，用户自行选择模型。单价按原币 / 百万 Token 填写；价格、汇率或凭证缺失不能开放云端服务。协议能力须实际测试确认。</p>
    <p className="text-xs text-text-dim">停用后可再次启用；删除需先停用，只移除本应用服务配置，历史用量保留。</p>
    {models.map(m => <div key={m.id} className="rounded border border-border p-2 text-sm">
      <p>{m.id} · {m.model} · v{m.version} · {m.local ? '本地' : '云端'} · {m.enabled ? '已开放' : '已停用'}
        {m.checks?.map(check => <span key={check.capability} role="status" className={`ml-3 ${check.success ? 'text-bull' : 'text-bear'}`}>
          {capabilities[check.capability]}测试{check.success ? '通过' : '未通过'}
          {!check.success && check.reason ? `：${check.reason}` : ''}
        </span>)}
      </p>
      <p>{m.capabilities.map(c => capabilities[c]).join(' / ')} · {m.validation_status || '尚未真实联调'}</p>
      {!m.local && <p className="text-xs text-text-dim">API Key：{m.has_key ? '已保存（不回显）' : '未配置'}</p>}
      <button type="button" disabled={busy} onClick={() => { setEditing(m); setProtocol(m.protocol); setEndpoint(m.endpoint) }} className={`mr-3 ${settingsButton.secondary}`}>编辑 {m.id}</button>
      <button type="button" disabled={busy || saving} aria-label={`${m.enabled ? '停用' : '启用'} ${m.id}`} onClick={() => manageModel(m)} className={`mr-3 ${settingsButton.secondary}`}>{m.enabled ? '停用' : '启用'}</button>
      <button type="button" disabled={busy || saving || m.enabled} aria-label={`删除 ${m.id}`} title={m.enabled ? '请先停用，再删除' : '删除模型服务配置'} onClick={() => manageModel(m, true)} className={`mr-3 ${settingsButton.danger}`}>删除</button>
      {m.capabilities.map(capability => <button key={capability} type="button" disabled={busy} onClick={async () => {
        if (!confirm(`将对 ${m.id} 发送测试请求，云端测试可能产生费用并占用全局预算。继续？`)) return
        await execute(`/api/models/${encodeURIComponent(m.id)}/test`, 'POST', { capability })
        // 成功与失败都重新读取当前配置版本的检查记录，避免旧成功提示残留。
        try { await reload() }
        catch { setValidationError('测试结果刷新失败，请刷新页面查看。') }
      }} className={`mr-3 ${settingsButton.secondary}`}>测试{capabilities[capability]}</button>)}
    </div>)}
    <form key={editing ? `${editing.id}-${editing.version}` : 'new'} onSubmit={submit} onInvalid={event => {
      setResult('')
      const field = event.target
      // 原生校验会阻止 submit，需在保存按钮旁说明未发起请求的原因。
      const firstInvalid = event.currentTarget.querySelector(':invalid')
      if (field === firstInvalid) setValidationError(`${field.labels?.[0]?.textContent || '模型配置'}：${field.validationMessage}`)
    }} onChange={() => { setResult(''); setValidationError('') }} className="grid gap-3 sm:grid-cols-2">
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
      <label>API Key（留空保留）<input name="api_key" type="password" autoComplete="new-password" aria-describedby="model-key-help" defaultValue={editing?.has_key ? savedKeyMask : ''} onFocus={event => { if (editing?.has_key && event.target.value === savedKeyMask) event.target.select() }} placeholder="输入 API Key" className={inputClass} /></label>
      <p id="model-key-help" className="sm:col-span-2 text-xs text-text-dim">{editing?.has_key ? '星号表示密钥已保存，不是真实密钥。保持星号或留空保存会保留原密钥，填写新值才替换。' : '保存后的密钥不会回显，编辑时留空会保留原密钥。'}</p>
      {protocol !== 'ollama' && <>
        <label>原币<select name="currency" defaultValue={editing?.price?.currency || 'CNY'} className={inputClass}><option>CNY</option><option>USD</option></select></label>
        {(protocol === 'deepseek' ? ['exchange_rate', 'cache_read', 'input', 'output'] : ['exchange_rate', 'input', 'output', 'cache_read', 'cache_write']).map(k => <label key={k}>{({ exchange_rate: '人民币汇率', input: protocol === 'deepseek' ? '缓存未命中输入单价' : '输入单价', output: '输出单价', cache_read: protocol === 'deepseek' ? '缓存命中输入单价' : '缓存读取单价', cache_write: '缓存写入单价' })[k]}<input name={k} required type="number" min={k === 'exchange_rate' ? '0.000001' : '0'} step="any" defaultValue={editing?.price?.[k] ?? ''} className={inputClass} /></label>)}
        {protocol !== 'deepseek' && <label>一小时缓存写入单价（可选）<input name="cache_write_1h" type="number" min="0" step="any" defaultValue={editing?.price?.cache_write_1h ?? ''} className={inputClass} /></label>}
      </>}
      <fieldset className="sm:col-span-2"><legend>开放能力</legend>{Object.entries(capabilities).map(([key, label]) => <label key={key} className="mr-4"><input type="checkbox" name={key} defaultChecked={editing ? editing.capabilities.includes(key) : key === 'text'} /> {label}</label>)}</fieldset>
      <label><input type="checkbox" name="enabled" defaultChecked={editing ? editing.enabled : true} /> 开放此模型</label>
      <div className="sm:col-span-2 flex flex-wrap items-center gap-3"><button type="submit" disabled={busy || saving} className={settingsButton.primary}>{saving ? '正在保存模型…' : '保存模型'}</button><SaveStatus busy={saving} message={result} error={validationError || error} />{editing && <button type="button" disabled={busy || saving} onClick={() => { setEditing(null); setProtocol('ollama'); setEndpoint(defaultEndpoints.ollama) }} className={settingsButton.secondary}>取消编辑</button>}</div>
    </form>
  </section>
}

export default function ModelSettings({ admin = false, managementOnly = false }) {
  const [models, setModels] = useState([])
  const [prefs, setPrefs] = useState(null)
  const [pendingOptions, setPendingOptions] = useState({})
  const [preferenceScene, setPreferenceScene] = useState('')
  const [error, setError] = useState('')
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(false)
  const [action, setAction] = useState('')
  const llm = useLLMStatus(busy && action.endsWith('/test'))
  const reload = async () => setModels((await fetchJSON('/api/models')).models)
  useEffect(() => {
    let active = true
    Promise.all([fetchJSON('/api/models'), managementOnly ? Promise.resolve(null) : fetchJSON(preferencesPath)]).then(([catalog, personal]) => {
      if (active) { setModels(catalog.models); setPrefs(personal) }
    }).catch(e => { if (active) setError(e.message) })
    return () => { active = false }
  }, [managementOnly])
  const execute = async (path, method, body) => {
    setBusy(true); setError(''); setStatus(''); setAction(path)
    try { return await fetchJSON(path, { method, body: JSON.stringify(body), headers: llm.headers }) }
    catch (e) { setError(e.message); return null }
    finally { setBusy(false) }
  }
  const savePreferences = async (changes, scene = '') => {
    const next = { ...prefs, ...changes }
    setPreferenceScene(scene)
    if (scene) setPrefs(next)
    const saved = await execute(preferencesPath, 'PUT', {
      local_only: next.local_only,
      scenes: next.scenes,
      automatic_ai_enabled: next.automatic_ai_enabled,
      expected_version: prefs.version,
    })
    if (saved) {
      setPrefs(saved)
      if (!scene) setPendingOptions({})
      setStatus(scene ? '模型选择已保存' : '个人 AI 设置已保存')
    } else if (scene) {
      setPrefs(prefs)
    }
  }
  const changeOption = (key, checked) => {
    setPendingOptions({ ...pendingOptions, [key]: checked })
    if (!preferenceScene) setStatus('')
  }
  const preferenceFeedback = action === preferencesPath
    ? <SaveStatus busy={busy} message={status} error={error} />
    : null
  return <section aria-labelledby="model-settings-title" className="border-t border-border pt-4 space-y-3">
    <h3 id="model-settings-title" className="text-sm font-semibold">{managementOnly ? '模型服务' : '我的 AI 设置'}</h3>
    {!managementOnly && <>
    <p className="text-xs text-text-dim">只按你选择的主模型和备用顺序调用。仅本地模式仍可使用在线行情和资讯；无合格本地模型时 AI 暂不可用，手工理财功能照常使用。</p>
    {prefs ? <div className="space-y-3">
      <label className="block"><input type="checkbox" disabled={busy} checked={pendingOptions.local_only ?? prefs.local_only} onChange={e => changeOption('local_only', e.target.checked)} /> 仅本地推理（保存后阻止旧任务后续云端请求）</label>
      <label className="block"><input type="checkbox" disabled={busy} checked={pendingOptions.automatic_ai_enabled ?? prefs.automatic_ai_enabled} onChange={e => changeOption('automatic_ai_enabled', e.target.checked)} /> 启用本人自动 AI 任务（可能产生费用，错过执行窗口不补跑）</label>
      <div className="flex flex-wrap items-center gap-3"><button type="button" disabled={busy} className={settingsButton.primary} onClick={() => savePreferences(pendingOptions)}>保存我的设置</button>{!preferenceScene && preferenceFeedback}</div>
      <p className="text-xs text-text-dim">主模型和备用模型在选择后自动保存。</p>
      <div className="grid gap-3 sm:grid-cols-2">{Object.keys(sceneNames).map(scene => <ModelChoices key={scene} scene={scene} ids={prefs.scenes[scene] || []} models={models} disabled={busy} onChange={ids => savePreferences({ scenes: { ...prefs.scenes, [scene]: ids } }, scene)}>
        {preferenceScene === scene && preferenceFeedback}
      </ModelChoices>)}</div>
    </div> : <p role="status">正在读取 AI 设置…</p>}
    </>}
    {busy && action.endsWith('/test') && <p role="status" className="text-xs text-text-dim">{llm.label}</p>}
    {admin && <ModelServices showTitle={!managementOnly} models={models} reload={reload} execute={execute} busy={busy} error={action && action !== preferencesPath ? error : ''} />}
    {!action && error && <p role="alert">{error}</p>}
  </section>
}
