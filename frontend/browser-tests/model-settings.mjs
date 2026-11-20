// 使用真实浏览器验证表单提交；默认模拟 API，--real-api 使用临时数据库真实后端。
// 两种模式均不调用模型或修改用户配置。
// 运行：NODE_PATH=<含 playwright 的 node_modules> node frontend/browser-tests/model-settings.mjs
// 完整链路：同一命令末尾加 --real-api；需项目 venv 已安装后端依赖。
// 设置页层级：再加 --settings-layout；可用 SETTINGS_SCREENSHOT 指定截图输出路径。
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { mkdtemp, writeFile, rm } from 'node:fs/promises'
import { basename } from 'node:path'
import { createServer } from 'vite'
import { spawn } from 'node:child_process'
import { once } from 'node:events'

const { chromium } = createRequire(import.meta.url)('playwright')
const root = fileURLToPath(new URL('../', import.meta.url))
const realAPI = process.argv.includes('--real-api')
const timePricing = process.argv.includes('--time-pricing')
const userDeletion = process.argv.includes('--user-deletion')
const usageDetails = process.argv.includes('--usage-details')
const searchCheckboxes = process.argv.includes('--search-checkboxes')
const adminNavigation = process.argv.includes('--admin-navigation')
const settingsScroll = process.argv.includes('--settings-scroll')
const settingsLimits = process.argv.includes('--settings-limits')
const settingsLayout = process.argv.includes('--settings-layout') || settingsScroll || settingsLimits || adminNavigation || searchCheckboxes || usageDetails || userDeletion
const fixture = await mkdtemp(`${root}.model-save-test-`)
const fixturePath = `/${basename(fixture)}`
await writeFile(`${fixture}/index.html`, '<!doctype html><html><body><div id="root"></div><script type="module" src="./entry.jsx"></script></body></html>')
await writeFile(`${fixture}/entry.jsx`, `
  import React from 'react';
  import { createRoot } from 'react-dom/client';
  import ModelSettings from '../src/components/ModelSettings.jsx';
  import AdminPage from '../src/components/AdminPage.jsx';
  import UsagePanel from '../src/components/UsagePanel.jsx';
  import App from '../src/App.jsx';
  ${settingsLayout || timePricing ? "import '../src/index.css';" : ''}
  import { setSessionToken } from '../src/hooks/sessionRuntime.js';
  ${realAPI ? `const login = await fetch('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(new URLSearchParams(location.search).has('ordinary') ? { username: 'scroll-user', password: 'Synthetic-scroll-current-02!' } : { username: 'model-tester', password: 'Synthetic-model-test-01!' }) });
  const session = await login.json();
  if (!login.ok) throw new Error('合成账号登录失败：' + JSON.stringify(session));
  setSessionToken(session.csrf_token);` : ''}
  createRoot(document.getElementById('root')).render(${usageDetails ? '<UsagePanel admin />' : settingsLimits ? '<AdminPage admin page="admin-usage" title="用量与预算" />' : settingsLayout ? '<App />' : '<ModelSettings admin />'});
`)
const server = await createServer({ root, server: { host: '127.0.0.1', port: 18767, strictPort: true,
  ...(realAPI ? { proxy: { '/api': { target: 'http://127.0.0.1:18768', changeOrigin: false } } } : {}) } })
let browser
let backend
try {
  if (realAPI) {
    backend = spawn(fileURLToPath(new URL('../../venv/bin/python', import.meta.url)),
      [fileURLToPath(new URL('./model-server.py', import.meta.url))], { cwd: `${root}..`, stdio: 'inherit' })
    let ready = false
    for (let attempt = 0; attempt < 100; attempt++) {
      try { ready = (await fetch('http://127.0.0.1:18768/api/health')).ok } catch {}
      if (ready) break
      await new Promise(resolve => setTimeout(resolve, 100))
    }
    assert.ok(ready, '隔离后端未就绪')
  }
  await server.listen()
  browser = await chromium.launch({ headless: true, channel: 'chrome' })
  const page = await browser.newPage()
  const errors = []
  page.on('pageerror', e => errors.push(e.message))
  if (realAPI) page.on('response', response => {
    if (response.url().includes('/api/') && response.status() >= 400) console.log('API', response.status(), new URL(response.url()).pathname)
  })
  let models = []
  let writes = 0
  let rejectSave = false
  let releaseSave
  if (!realAPI) await page.route('**/api/models**', async route => {
    const request = route.request()
    if (request.url().endsWith('/preferences')) {
      return route.fulfill({ json: { local_only: true, automatic_ai_enabled: false, scenes: {}, version: 0 } })
    }
    if (request.method() === 'PUT') {
      writes++
      await new Promise(resolve => { releaseSave = resolve })
      if (rejectSave) return route.fulfill({ status: 409, json: { detail: '配置已被修改，请刷新后重试' } })
      const data = request.postDataJSON()
      models = [{ ...data, api_key: undefined, version: 1 }]
      return route.fulfill({ json: models[0] })
    }
    return route.fulfill({ json: { models } })
  })
  if (adminNavigation) {
    const login = await page.request.post('http://127.0.0.1:18768/api/auth/login', { data: { username: 'scroll-user', password: 'Synthetic-scroll-01!' } })
    const session = await login.json()
    const changed = await page.request.post('http://127.0.0.1:18768/api/auth/password', { headers: { 'X-CSRF-Token': session.csrf_token }, data: { current_password: 'Synthetic-scroll-01!', new_password: 'Synthetic-scroll-current-02!' } })
    assert.equal(changed.status(), 204)
  }
  if (usageDetails) await page.route(/\/api\/usage(?:\?.*)?$/, async route => {
    const response = await route.fetch()
    const data = await response.json()
    data.details = Array.from({ length: 25 }, (_, index) => ({ id: String(index), scene: 'ask', model_id: `synthetic-${index}`, input_tokens: 1, output_tokens: 1, state: 'settled', cost: 0 }))
    await route.fulfill({ response, json: data })
  })
  await page.goto(`http://127.0.0.1:18767${fixturePath}/index.html${settingsLayout ? '#settings' : ''}`)
  if (timePricing) {
    assert.ok(realAPI, '分时价格验收须使用隔离真实 API')
    await page.locator('form').filter({ has: page.locator('input[name="id"]') }).locator('select').first().selectOption('deepseek')
    assert.equal(await page.locator('input[name="model"]').inputValue(), 'deepseek-flash')
    assert.equal(await page.locator('input[name="input"]').inputValue(), '2')
    assert.equal(await page.locator('input[name="off_peak_cache_read"]').inputValue(), '0.02')
    assert.equal(await page.locator('input[name="currency"]').count(), 0)
    assert.equal(await page.locator('input[name="exchange_rate"]').count(), 0)
    await page.locator('input[name="model"]').fill('deepseek-v4-pro')
    assert.equal(await page.locator('input[name="input"]').inputValue(), '9')
    assert.equal(await page.locator('input[name="off_peak_output"]').inputValue(), '13.5')
    await page.locator('input[name="id"]').fill('timed-model')
    await page.locator('input[name="api_key"]').fill('synthetic-price-key')
    const save = page.getByRole('button', { name: '保存模型', exact: true })
    await save.click()
    await page.getByRole('status').filter({ hasText: '模型 timed-model 已保存' }).waitFor()
    const saved = (await (await page.request.get('http://127.0.0.1:18767/api/models')).json()).models.find(model => model.id === 'timed-model')
    assert.deepEqual(saved.time_pricing.off_peak, { input: '4.5', output: '13.5', cache_read: '0.15' })
    assert.equal(saved.price.input, '9')
    assert.equal(saved.price.currency, 'CNY')
    assert.equal(saved.price.exchange_rate, '1')
    assert.equal(saved.api_key, undefined)
    await page.reload()
    await page.getByRole('button', { name: '编辑 timed-model', exact: true }).click()
    assert.equal(await page.locator('input[name="off_peak_output"]').inputValue(), '13.5')
    for (const width of [320, 768, 1440]) {
      await page.setViewportSize({ width, height: 900 })
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1))
    }
    await page.locator('input[name="off_peak_output"]').fill('14')
    await save.click()
    await page.getByRole('status').filter({ hasText: '模型 timed-model 已保存' }).waitFor()
    const updated = (await (await page.request.get('http://127.0.0.1:18767/api/models')).json()).models.find(model => model.id === 'timed-model')
    assert.equal(updated.time_pricing.off_peak.output, '14')
    assert.deepEqual(errors, [])
    console.log('PASS: 官方价格预填、双模型切换、保存读回、人民币计价和响应式布局')
  } else if (userDeletion) {
    await page.locator('aside').getByRole('button', { name: '账号管理', exact: true }).click()
    const remove = page.getByRole('button', { name: '删除 scroll-user', exact: true })
    await remove.waitFor()
    assert.ok(await remove.isDisabled(), '活动账号必须先停用')
    assert.equal(await page.getByRole('button', { name: '删除 model-tester', exact: true }).count(), 0)
    await page.getByRole('button', { name: '停用 scroll-user', exact: true }).click()
    await page.getByRole('button', { name: '启用 scroll-user', exact: true }).waitFor()
    await remove.click()
    const confirmation = page.getByRole('form', { name: '删除用户确认', exact: true })
    const permanent = confirmation.getByRole('button', { name: '永久删除账号与个人数据', exact: true })
    assert.ok(await permanent.isDisabled())
    await confirmation.getByLabel('输入用户名确认删除', { exact: true }).fill('wrong')
    assert.ok(await permanent.isDisabled(), '确认名不符时禁止删除')
    await confirmation.getByRole('button', { name: '取消删除', exact: true }).click()
    assert.equal(await confirmation.count(), 0)
    assert.equal(await remove.count(), 1, '取消删除保留账号')
    await remove.click()
    await confirmation.getByLabel('输入用户名确认删除', { exact: true }).fill('scroll-user')
    await permanent.click()
    await page.getByRole('status').filter({ hasText: '账号及个人数据已删除' }).waitFor()
    assert.equal(await remove.count(), 0)
    await page.reload()
    await page.getByRole('heading', { name: '账号管理', level: 2, exact: true }).waitFor()
    assert.equal(await remove.count(), 0, '刷新后不再显示被删除用户')
    await page.getByLabel('账号名', { exact: true }).fill('scroll-user')
    await page.getByLabel('临时密码', { exact: true }).fill('Synthetic-recreated-01!')
    await page.getByRole('button', { name: '创建账号', exact: true }).click()
    await remove.waitFor()
    assert.ok(await remove.isDisabled(), '同名新账号是新身份且为活动状态')
    const users = (await (await page.request.get('http://127.0.0.1:18767/api/admin/users')).json()).users
    assert.ok(users.find(user => user.username === 'scroll-user').id > 2)
    assert.deepEqual(errors, [])
    console.log('PASS: 管理员不可删、活动用户须停用、用户名确认、取消、删除刷新及同名新身份')
  } else if (usageDetails) {
    const table = page.getByRole('table', { name: '近期调用记录', exact: true })
    await table.waitFor()
    assert.equal(await table.locator('tbody tr').count(), 20, '默认只展示20条')
    const expand = page.getByRole('button', { name: '展开全部（共 25 条）', exact: true })
    assert.equal(await expand.getAttribute('aria-expanded'), 'false')
    await expand.click()
    assert.equal(await table.locator('tbody tr').count(), 25)
    const collapse = page.getByRole('button', { name: '收起（仅显示 20 条）', exact: true })
    assert.equal(await collapse.getAttribute('aria-expanded'), 'true')
    await collapse.click()
    assert.equal(await table.locator('tbody tr').count(), 20)
    await expand.click()
    await page.getByLabel('查看全局计量元数据', { exact: true }).uncheck()
    await expand.waitFor()
    assert.equal(await table.locator('tbody tr').count(), 20, '切换个人与全局后恢复默认收起')
    await page.reload()
    await expand.waitFor()
    assert.equal(await table.locator('tbody tr').count(), 20, '刷新后默认收起')
    assert.deepEqual(errors, [])
    console.log('PASS: 近期调用默认20条，展开全部、收起，以及切换范围/刷新后的默认收起')
  } else if (searchCheckboxes) {
    await page.locator('aside').getByRole('button', { name: '数据与搜索', exact: true }).click()
    const fields = ['free_confirmed', 'auto_pay_disabled', 'exclusive_key', 'storage_allowed', 'enabled']
    for (const provider of ['tavily', 'exa']) {
      const save = page.getByRole('button', { name: `保存 ${provider} 配置`, exact: true })
      const form = save.locator('..').locator('..')
      await form.locator('input[name="key"]').fill(`synthetic-${provider}-checkbox-key`)
      await form.locator('input[name="remaining"]').fill('100')
      await form.locator('input[name="verified_until"]').fill('2099-01-01T00:00')
      if (provider === 'tavily') {
        for (const field of ['free_confirmed', 'auto_pay_disabled', 'enabled']) await form.locator(`input[name="${field}"]`).check()
        await save.click()
        const error = save.locator('..').getByRole('alert')
        await error.filter({ hasText: '填写核验依据' }).waitFor()
        assert.ok((await error.textContent()).includes('凭证仅本应用使用'))
        assert.ok((await error.textContent()).includes('保存问答引用'))
        assert.ok(!(await error.textContent()).includes('synthetic-tavily-checkbox-key'))
      }
      await form.locator('input[name="evidence"]').fill('合成免费条件核验，仅做配置回归')
      for (const field of fields) await form.locator(`input[name="${field}"]`).check()
      await save.click()
      await save.locator('..').getByRole('status').filter({ hasText: '已保存' }).waitFor()
    }
    const persisted = await (await page.request.get('http://127.0.0.1:18767/api/search/channels')).json()
    for (const channel of persisted.channels) {
      for (const field of fields) assert.equal(channel[field], true, `${channel.provider} 的 ${field} 应已保存到后端`)
    }
    await page.reload()
    for (const provider of ['tavily', 'exa']) {
      const save = page.getByRole('button', { name: `保存 ${provider} 配置`, exact: true })
      const form = save.locator('..').locator('..')
      await form.getByPlaceholder('********', { exact: true }).waitFor()
      for (const field of fields) assert.equal(await form.locator(`input[name="${field}"]`).isChecked(), true, `${provider} 刷新后应回填 ${field}`)
      assert.equal(await form.locator('input[name="remaining"]').inputValue(), '100', '额度也应回填')
      // 取消开放和一个核验项，再次保存并刷新，验证 false 同样能持久化。
      await form.locator('input[name="enabled"]').uncheck()
      await form.locator('input[name="storage_allowed"]').uncheck()
      await save.click()
      await save.locator('..').getByRole('status').filter({ hasText: '已保存' }).waitFor()
    }
    await page.reload()
    for (const provider of ['tavily', 'exa']) {
      const form = page.getByRole('button', { name: `保存 ${provider} 配置`, exact: true }).locator('..').locator('..')
      await form.getByPlaceholder('********', { exact: true }).waitFor()
      for (const field of fields) assert.equal(await form.locator(`input[name="${field}"]`).isChecked(), !['enabled', 'storage_allowed'].includes(field), `${provider} 应保留每项的勾选状态`)
    }
    const sourceForm = page.getByRole('button', { name: '保存来源策略', exact: true }).locator('..').locator('..')
    await sourceForm.getByLabel('同花顺', { exact: true }).uncheck()
    await page.getByRole('button', { name: '保存来源策略', exact: true }).click()
    await page.getByRole('status').filter({ hasText: '来源策略已保存' }).waitFor()
    await page.reload()
    await page.getByRole('button', { name: '保存来源策略', exact: true }).waitFor()
    assert.equal(await sourceForm.getByLabel('同花顺', { exact: true }).isChecked(), false)
    assert.deepEqual(errors, [])
    console.log('PASS: Tavily/Exa 全部复选框保存刷新回填、取消勾选持久化、额度回填及来源复选框保存')
  } else if (adminNavigation) {
    const personal = () => page.getByRole('heading', { name: '设置', level: 2, exact: true })
    await personal().waitFor()
    await page.getByRole('heading', { name: '我的 AI 设置', exact: true }).waitFor()
    assert.equal(await page.getByRole('button', { name: '保存模型', exact: true }).count(), 0)
    assert.equal(await page.getByRole('button', { name: '保存额度', exact: true }).count(), 0)
    assert.equal(await page.getByRole('button', { name: '保存消息保留策略', exact: true }).count(), 0)
    const entries = [
      ['admin-users', '账号管理', '创建账号'],
      ['admin-models', '模型服务', '保存模型'],
      ['admin-usage', '用量与预算', '保存额度'],
      ['admin-data', '数据与搜索', '保存来源策略'],
      ['admin-system', '系统配置', '保存消息保留策略'],
      ['admin-operations', '备份与维护', '创建备份'],
    ]
    for (const [key, label, button] of entries) {
      await page.locator('aside').getByRole('button', { name: label, exact: true }).click()
      await page.getByRole('heading', { name: label, level: 2, exact: true }).waitFor()
      await page.getByRole('button', { name: button, exact: true }).waitFor()
      assert.ok(page.url().endsWith('#' + key))
      assert.equal(await page.getByRole('button', { name: '保存我的设置', exact: true }).count(), 0, '管理页不混入个人偏好')
    }
    // 验证迁移后的表单仍可保存，不发送模型请求或外部通知。
    await page.locator('aside').getByRole('button', { name: '用量与预算', exact: true }).click()
    const subject = page.getByLabel('预算对象', { exact: true })
    await subject.selectOption('1')
    const day = page.getByLabel('日额度', { exact: true })
    await day.fill('2'); await page.getByLabel('月额度', { exact: true }).fill('2')
    const save = page.getByRole('button', { name: '保存额度', exact: true })
    await save.click()
    await save.locator('..').getByRole('status').filter({ hasText: '额度已保存' }).waitFor()
    await page.reload()
    await page.getByRole('heading', { name: '用量与预算', level: 2, exact: true }).waitFor()
    await subject.selectOption('1')
    await page.waitForFunction(() => document.querySelector('input[name="day"]').value === '2')
    await page.locator('aside').getByRole('button', { name: '系统配置', exact: true }).click()
    const policy = page.getByRole('button', { name: '保存消息保留策略', exact: true })
    await policy.click()
    await policy.locator('..').getByRole('status').filter({ hasText: '已保存' }).waitFor()
    assert.equal(await page.getByRole('button', { name: '清除本人凭证', exact: true }).count(), 0)
    for (const width of [1440, 768, 390]) {
      await page.setViewportSize({ width, height: 900 })
      await page.screenshot({ path: `/tmp/licai-admin-system-${width}.png`, animations: 'disabled' })
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), '管理页不应撑出文档横向滚动')
    }
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goBack()
    await page.getByRole('heading', { name: '用量与预算', level: 2, exact: true }).waitFor()
    // 切换到另一个真实合成账号，验证普通账号的菜单、页面与接口权限。
    const requests = []
    page.on('request', request => requests.push(new URL(request.url()).pathname))
    await page.goto(`http://127.0.0.1:18767${fixturePath}/index.html?ordinary=1#admin-users`)
    await page.getByRole('alert').filter({ hasText: '此页面仅管理员可访问' }).waitFor()
    for (const [, label] of entries) assert.equal(await page.locator('aside').getByRole('button', { name: label, exact: true }).count(), 0)
    assert.equal(requests.filter(path => path.startsWith('/api/admin/')).length, 0, '被拒绝的管理页不请求管理数据')
    for (const path of ['/api/admin/users', '/api/usage/policy', '/api/notifications/policy']) {
      assert.equal((await page.request.get('http://127.0.0.1:18767' + path)).status(), 403)
    }
    await page.locator('aside').getByRole('button', { name: '设置', exact: true }).click()
    await personal().waitFor()
    await page.getByRole('button', { name: '保存我的设置', exact: true }).waitFor()
    await page.getByRole('heading', { name: '我的用量', exact: true }).waitFor()
    await page.getByRole('button', { name: '保存我的设置', exact: true }).click()
    await page.getByRole('status').filter({ hasText: '个人 AI 设置已保存' }).waitFor()
    assert.equal(await page.getByRole('button', { name: '保存额度', exact: true }).count(), 0)
    assert.deepEqual(errors, [])
    console.log('PASS: 六个管理页面、预算与通知策略保存、刷新/后退、多尺寸，以及普通账号菜单/页面/接口隔离与个人设置')
  } else if (settingsLimits) {
    const save = page.getByRole('button', { name: '保存额度', exact: true })
    const subject = page.getByLabel('预算对象', { exact: true })
    await subject.getByRole('option', { name: '1 · model-tester（管理员）', exact: true }).waitFor({ state: 'attached' })
    assert.equal(await subject.getByRole('option', { name: '2 · scroll-user', exact: true }).count(), 1)
    assert.equal(await subject.getByRole('option', { name: '0 · 全局', exact: true }).count(), 1)
    const day = page.getByLabel('日额度', { exact: true })
    const month = page.getByLabel('月额度', { exact: true })
    await day.fill('12.345678')
    await month.fill('99.1')
    await save.click()
    await save.locator('..').getByRole('status').filter({ hasText: '额度已保存' }).waitFor()
    const stored = await (await page.request.get('http://127.0.0.1:18768/api/usage?subject=0')).json()
    assert.equal(stored.budget.user.day.limit, 12345678, '额度已写入数据库')
    await page.reload()
    await save.waitFor()
    await page.waitForFunction(() => !document.querySelector('input[name="day"]').disabled)
    assert.equal(await day.inputValue(), '12.345678', '刷新后应回填保存的日额度')
    assert.equal(await month.inputValue(), '99.1', '刷新后应回填保存的月额度')
    await Promise.all([page.waitForResponse(r => r.url().endsWith('/api/usage?subject=2')), subject.selectOption('2')])
    await page.waitForFunction(() => !document.querySelector('input[name="day"]').disabled)
    assert.equal(await day.inputValue(), '', '新用户不沿用全局额度')
    await day.fill('0')
    await month.fill('')
    await save.click()
    await save.locator('..').getByRole('status').filter({ hasText: '额度已保存' }).waitFor()
    await Promise.all([page.waitForResponse(r => r.url().endsWith('/api/usage?subject=0')), subject.selectOption('0')])
    await page.waitForFunction(() => !document.querySelector('input[name="day"]').disabled)
    assert.equal(await day.inputValue(), '12.345678')
    await Promise.all([page.waitForResponse(r => r.url().endsWith('/api/usage?subject=2')), subject.selectOption('2')])
    await page.waitForFunction(() => !document.querySelector('input[name="day"]').disabled)
    assert.equal(await day.inputValue(), '0', '0 额度应保持为 0')
    assert.equal(await month.inputValue(), '', '未配置额度应保持空白')
    await Promise.all([page.waitForResponse(r => r.url().endsWith('/api/usage?subject=0')), subject.selectOption('0')])
    await page.waitForFunction(() => !document.querySelector('input[name="day"]').disabled)
    let releaseLate, lateStarted, lateFinished
    const started = new Promise(resolve => { lateStarted = resolve })
    const finished = new Promise(resolve => { lateFinished = resolve })
    await page.route('**/api/usage?subject=2', async route => {
      await new Promise(resolve => { releaseLate = resolve; lateStarted() })
      await route.fulfill({ json: { budget: { user: { day: { limit: 0 }, month: { limit: null } } } } })
      lateFinished()
    })
    await subject.selectOption('2')
    await started
    assert.ok(await save.isDisabled(), '读取额度期间不能保存空表单')
    await Promise.all([page.waitForResponse(r => r.url().endsWith('/api/usage?subject=0')), subject.selectOption('0')])
    await page.waitForFunction(() => !document.querySelector('input[name="day"]').disabled)
    releaseLate()
    await finished
    assert.equal(await day.inputValue(), '12.345678', '迟到响应不得覆盖另一对象的额度')
    await page.unroute('**/api/usage?subject=2')
    await page.route('**/api/usage?subject=2', route => route.fulfill({ status: 503, json: { detail: '合成读取失败' } }))
    await subject.selectOption('2')
    await save.locator('..').getByRole('alert').filter({ hasText: '额度读取失败' }).waitFor()
    assert.ok(await save.isDisabled(), '读取失败不能保存空值覆盖现有额度')
    await page.unroute('**/api/usage?subject=2')

    await Promise.all([page.waitForResponse(r => r.url().endsWith('/api/usage?subject=1')), subject.selectOption('1')])
    await page.waitForFunction(() => !document.querySelector('input[name="day"]').disabled)
    assert.equal(await day.inputValue(), '', '管理员的个人预算也应单独读取')
    assert.equal(await subject.inputValue(), '1')
    if (process.env.SETTINGS_SCREENSHOT) await save.locator('..').locator('..').screenshot({ path: process.env.SETTINGS_SCREENSHOT })
    await page.route('**/api/admin/users', route => route.fulfill({ status: 503, json: { detail: '合成账号列表读取失败' } }))
    await page.reload()
    await page.getByRole('alert').filter({ hasText: '账号列表读取失败：' }).waitFor()
    assert.equal(await subject.getByRole('option', { name: '0 · 全局', exact: true }).count(), 1, '列表读取失败仍保留全局选项')
    assert.deepEqual(errors, [])
    console.log('PASS: 编号与用户名、管理员标记、账号列表失败提示、额度保存回填与账号隔离')
  } else if (settingsScroll) {
    await page.getByRole('heading', { name: '我的 AI 设置', exact: true }).waitFor()
    for (const width of [1440, 768, 320]) {
      await page.setViewportSize({ width, height: 900 })
      const scroller = page.locator('main > .overflow-y-auto')
      await scroller.evaluate(e => { e.scrollTop = e.scrollHeight })
      const dimensions = await page.evaluate(() => ({ documentHeight: document.documentElement.scrollHeight, viewport: innerHeight, windowScroll: scrollY }))
      assert.ok(dimensions.documentHeight <= dimensions.viewport + 1, `${width}px 设置页不应撑大外层文档：${JSON.stringify(dimensions)}`)
      const box = await scroller.boundingBox()
      await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
      await page.mouse.wheel(0, 1800)
      await page.mouse.wheel(0, -900)
      await page.screenshot({ path: `/tmp/licai-settings-scroll-${width}.png`, animations: 'disabled' })
      assert.equal(await page.evaluate(() => scrollY), 0, '到底后继续滚动不应带动外层文档')
      assert.ok(await page.getByRole('heading', { name: '理财助手', exact: true }).isVisible(), '回滚后顶栏仍应可见')
    }
    assert.deepEqual(errors, [])
    console.log('PASS: 含普通用户的完整设置页，底部连续滚动和向上回滚无外层空白，1440/768/320px通过')
  } else if (settingsLayout) {
    await page.getByRole('heading', { name: '设置', level: 2, exact: true }).waitFor({ timeout: 3000 })
    const push = page.getByRole('region', { name: '推送设置', exact: true })
    await push.getByRole('heading', { name: '推送设置', level: 3, exact: true }).waitFor()
    await push.getByRole('combobox', { name: /^渠道类型/ }).selectOption('feishu')
    await push.getByLabel('飞书 Webhook URL', { exact: true }).waitFor()
    assert.equal(await push.getByRole('button', { name: '保存渠道', exact: true }).count(), 1)
    assert.equal(await push.getByRole('button', { name: /^发送测试/ }).count(), 0)
    assert.equal(await push.getByText('用量与预算', { exact: true }).count(), 0)
    await page.getByRole('heading', { name: '我的用量', level: 3, exact: true }).waitFor()
    await page.getByRole('heading', { name: '我的 AI 设置', level: 3, exact: true }).waitFor()
    assert.equal(await page.getByRole('button', { name: '创建账号', exact: true }).count(), 0)
    assert.equal(await page.getByRole('heading', { level: 2 }).count(), 1)
    await page.locator('aside').getByRole('button', { name: '数据与搜索', exact: true }).click()
    const sourceSave = page.getByRole('button', { name: '保存来源策略', exact: true })
    const sourceFeedback = sourceSave.locator('..')
    let releaseSource
    let rejectSource = false
    let requestArrived
    const sourceStarted = new Promise(resolve => { requestArrived = resolve })
    await page.route('**/api/settings/data-center', async route => {
      if (route.request().method() !== 'PUT') return route.continue()
      if (rejectSource) return route.fulfill({ status: 409, json: { detail: '来源策略版本冲突，请刷新后重试' } })
      await new Promise(resolve => { releaseSource = resolve; requestArrived() })
      return route.continue()
    })
    await sourceSave.click()
    await sourceStarted
    assert.ok(await sourceSave.isDisabled(), '保存期间禁止重复提交')
    await sourceFeedback.getByRole('status').filter({ hasText: '保存中' }).waitFor()
    releaseSource()
    await sourceFeedback.getByRole('status').filter({ hasText: '来源策略已保存' }).waitFor()
    await page.getByLabel('A 股实时价', { exact: true }).selectOption('tencent,sina')
    assert.equal((await sourceFeedback.getByRole('status').textContent()).trim(), '', '修改后清除旧成功状态')
    rejectSource = true
    await sourceSave.click()
    await sourceFeedback.getByRole('alert').filter({ hasText: '版本冲突' }).waitFor()
    assert.equal(await sourceFeedback.getByText('来源策略已保存', { exact: true }).count(), 0, '失败不显示已保存')
    await page.unroute('**/api/settings/data-center')
    await sourceSave.click()
    await sourceFeedback.getByRole('status').filter({ hasText: '来源策略已保存' }).waitFor()
    if (process.env.SAVE_FEEDBACK_SCREENSHOT) {
      await sourceSave.scrollIntoViewIfNeeded()
      await page.screenshot({ path: process.env.SAVE_FEEDBACK_SCREENSHOT, animations: 'disabled' })
    }

    for (const [target, name, text] of [['用量与预算', '保存额度', '额度已保存'], ['用量与预算', '保存用量策略', '已保存'], ['系统配置', '保存消息保留策略', '已保存'], ['设置', '保存我的设置', '个人 AI 设置已保存']]) {
      await page.locator('aside').getByRole('button', { name: target, exact: true }).click()
      const button = page.getByRole('button', { name, exact: true })
      await button.click()
      await button.locator('..').getByRole('status').filter({ hasText: text }).waitFor()
    }
    await page.locator('aside').getByRole('button', { name: '数据与搜索', exact: true }).click()
    const searchSave = page.getByRole('button', { name: '保存 tavily 配置', exact: true })
    const searchForm = searchSave.locator('..').locator('..')
    await searchForm.locator('input[name="key"]').fill('synthetic-search-key-never-send')
    await searchForm.locator('input[name="verified_until"]').fill('2099-01-01T00:00')
    await searchSave.click()
    await searchSave.locator('..').getByRole('status').filter({ hasText: '已保存' }).waitFor()
    assert.equal(await searchForm.locator('input[name="key"]').inputValue(), '', '保存后清除凭证输入')
    await searchSave.click()
    await searchSave.locator('..').getByRole('status').filter({ hasText: '已保存' }).waitFor()
    assert.equal(await searchForm.getByRole('alert').count(), 0, '刷新配置版本后可再次保存')
    await page.locator('aside').getByRole('button', { name: '设置', exact: true }).click()
    await push.getByRole('combobox', { name: /^渠道类型/ }).selectOption('pushplus')
    await push.getByLabel('配置名称', { exact: true }).fill('保存反馈合成渠道')
    await push.getByLabel('PushPlus Token', { exact: true }).fill('synthetic-token-never-send')
    const channelSave = push.getByRole('button', { name: '保存渠道', exact: true })
    await channelSave.click()
    await channelSave.locator('..').getByRole('status').filter({ hasText: '已保存' }).waitFor()
    assert.equal(await push.getByRole('button', { name: /^发送测试/ }).count(), 0, '保存反馈不能触发测试或自动启用')
    await push.getByLabel('配置名称', { exact: true }).fill('尚未保存的修改')
    assert.equal((await channelSave.locator('..').getByRole('status').textContent()).trim(), '')
    console.log('PASS: 保存按钮就近显示处理中、成功与失败，修改后清除旧成功；额度/策略/个人AI/通知均有反馈')

    await page.setViewportSize({ width: 390, height: 844 })
    assert.ok(await push.isVisible())
    await push.scrollIntoViewIfNeeded()
    if (process.env.SETTINGS_SCREENSHOT) await page.screenshot({ path: process.env.SETTINGS_SCREENSHOT, animations: 'disabled' })
    assert.deepEqual(errors, [])
    console.log('PASS: 设置总标题、预算/推送/AI同级、飞书控件归组及移动端呈现')
  } else {
  const save = page.getByRole('button', { name: '保存模型', exact: true })
  await save.waitFor()
  const protocol = page.getByRole('combobox', { name: /^协议/ })
  const endpoint = page.getByLabel('服务基础地址', { exact: true })
  for (const [name, address] of Object.entries({
    anthropic: 'https://api.anthropic.com', openai: 'https://api.openai.com',
    deepseek: 'https://api.deepseek.com', qwen: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    gemini: 'https://generativelanguage.googleapis.com', openai_compatible: '',
    ollama: 'http://localhost:11434',
  })) {
    await protocol.selectOption(name)
    assert.equal(await endpoint.inputValue(), address, `${name} 切换后应填入对应地址`)
    if (name === 'deepseek') {
      assert.equal(await page.getByLabel('缓存命中输入单价', { exact: true }).count(), 1)
      assert.equal(await page.getByLabel('缓存未命中输入单价', { exact: true }).count(), 1)
      assert.equal(await page.getByLabel('输出单价', { exact: true }).count(), 1)
      assert.equal(await page.locator('input[name="cache_write"], input[name="cache_write_1h"]').count(), 0)
      assert.equal(await page.getByLabel('缓存读取单价', { exact: true }).count(), 0)
    }
  }
  await endpoint.fill('http://127.0.0.1:11434')
  await page.getByLabel('模型标识', { exact: true }).fill('local-test')
  await page.getByLabel('厂商模型 ID', { exact: true }).fill('qwen3:4b')
  await save.click()
  if (realAPI) {
    try { await page.getByRole('status').filter({ hasText: '模型 local-test 已保存' }).waitFor({ timeout: 5000 }) }
    catch { throw new Error(`保存未成功：${(await page.getByRole('alert').allTextContents()).join('; ')}`) }
    await page.getByRole('button', { name: '编辑 local-test', exact: true }).waitFor()
    const localCard = page.getByRole('button', { name: '编辑 local-test', exact: true }).locator('..')
    page.once('dialog', dialog => dialog.accept())
    await localCard.getByRole('button', { name: '测试文本', exact: true }).click()
    await localCard.getByRole('status').filter({ hasText: '文本测试通过' }).waitFor({ timeout: 3000 })
    assert.equal(await page.getByLabel('问答主模型').locator('option[value="local-test"]').count(), 1)
    assert.equal(await page.getByLabel('问答主模型').inputValue(), '')
    const catalog = await (await page.request.get('http://127.0.0.1:18768/api/models')).json()
    assert.equal(catalog.models[0].model, 'qwen3:4b')
    await page.reload()
    await page.getByRole('button', { name: '编辑 local-test', exact: true }).waitFor()
    await localCard.getByRole('status').filter({ hasText: '文本测试通过' }).waitFor({ timeout: 3000 })
    assert.equal(await page.getByLabel('问答主模型').locator('option[value="local-test"]').count(), 1)
    await page.getByRole('button', { name: '编辑 local-test', exact: true }).click()
    assert.equal(await endpoint.inputValue(), 'http://127.0.0.1:11434', '编辑时应保留保存的自定义地址')
    await page.getByRole('button', { name: '取消编辑', exact: true }).click()
    assert.equal(await endpoint.inputValue(), 'http://localhost:11434')
    await protocol.selectOption('deepseek')
    await page.getByLabel('模型标识', { exact: true }).fill('deepseek-test')
    await page.getByLabel('厂商模型 ID', { exact: true }).fill('deepseek-flash')
    await page.getByLabel('API Key（留空保留）', { exact: true }).fill('synthetic-test-key')
    await page.getByLabel('人民币汇率', { exact: true }).fill('1')
    await page.getByLabel('缓存命中输入单价', { exact: true }).fill('0.04')
    await page.getByLabel('缓存未命中输入单价', { exact: true }).fill('2')
    await page.getByLabel('输出单价', { exact: true }).fill('8')
    await save.click()
    await page.getByRole('button', { name: '编辑 deepseek-test', exact: true }).waitFor()
    const deepseek = (await (await page.request.get('http://127.0.0.1:18768/api/models')).json()).models.find(m => m.id === 'deepseek-test')
    assert.equal(Number(deepseek.price.cache_read), 0.04)
    assert.equal(Number(deepseek.price.input), 2)
    assert.equal(Number(deepseek.price.output), 8)
    assert.equal(Number(deepseek.price.cache_write), 0)
    assert.equal(deepseek.price.cache_write_1h, null)
    assert.equal(deepseek.has_key, true)
    assert.equal(Object.hasOwn(deepseek, 'api_key'), false)
    const deepseekCard = page.getByRole('button', { name: '编辑 deepseek-test', exact: true }).locator('..')
    await deepseekCard.getByText('API Key：已保存（不回显）', { exact: true }).waitFor()
    page.once('dialog', dialog => dialog.accept())
    await deepseekCard.getByRole('button', { name: '测试文本', exact: true }).click()
    await deepseekCard.getByRole('status').filter({ hasText: '云端调用已暂停' }).waitFor()
    await page.getByRole('button', { name: '编辑 deepseek-test', exact: true }).click()
    const keyInput = page.getByLabel('API Key（留空保留）', { exact: true })
    assert.equal(await keyInput.inputValue(), '********')
    await page.getByText('星号表示密钥已保存，不是真实密钥。保持星号或留空保存会保留原密钥，填写新值才替换。', { exact: true }).waitFor()
    await page.getByLabel('缓存命中输入单价', { exact: true }).fill('0')
    const maskedSave = page.waitForRequest(request => request.method() === 'PUT' && new URL(request.url()).pathname === '/api/models')
    await save.click()
    assert.equal((await maskedSave).postDataJSON().api_key, '', '星号占位不能作为新密钥提交')
    await page.getByRole('status').filter({ hasText: '模型 deepseek-test 已保存' }).waitFor()
    await page.getByRole('button', { name: '编辑 deepseek-test', exact: true }).click()
    assert.equal(await page.getByLabel('缓存命中输入单价', { exact: true }).inputValue(), '0')
    assert.equal(await keyInput.inputValue(), '********')
    assert.equal((await page.locator('body').textContent()).includes('synthetic-test-key'), false)
    await keyInput.fill('synthetic-replacement-key')
    const replacementSave = page.waitForRequest(request => request.method() === 'PUT' && new URL(request.url()).pathname === '/api/models')
    await save.click()
    assert.equal((await replacementSave).postDataJSON().api_key, 'synthetic-replacement-key')
    await page.getByRole('status').filter({ hasText: '模型 deepseek-test 已保存' }).waitFor()
    await page.getByRole('button', { name: '编辑 deepseek-test', exact: true }).click()
    assert.equal(await keyInput.inputValue(), '********')
    await page.getByRole('button', { name: '取消编辑', exact: true }).click()
    assert.ok(await page.getByRole('button', { name: '删除 deepseek-test', exact: true }).isDisabled())
    const askModel = page.getByLabel('问答主模型')
    const localOnly = page.getByLabel(/仅本地推理/)
    const savedLocalOnly = await localOnly.isChecked()
    await localOnly.setChecked(!savedLocalOnly)
    const preferencesPath = '/api/models/preferences'
    const autoSave = page.waitForResponse(response => response.request().method() === 'PUT' && new URL(response.url()).pathname === preferencesPath)
    await askModel.selectOption('deepseek-test')
    const savedPreferences = await autoSave
    assert.ok(savedPreferences.ok())
    assert.deepEqual((await savedPreferences.json()).scenes.ask, ['deepseek-test'])
    assert.equal(savedPreferences.request().postDataJSON().local_only, savedLocalOnly, '自动保存模型不提交尚未保存的本地推理开关')
    assert.equal(await localOnly.isChecked(), !savedLocalOnly, '自动保存模型保留开关草稿')
    const askChoices = page.getByRole('group', { name: '问答', exact: true })
    await askChoices.getByRole('status').filter({ hasText: '模型选择已保存' }).waitFor()
    const failSave = route => route.request().method() === 'PUT'
      ? route.fulfill({ status: 409, json: { detail: '个人配置已更新，请刷新' } })
      : route.continue()
    await page.route(`**${preferencesPath}`, failSave)
    await askModel.selectOption('')
    await askChoices.getByRole('alert').filter({ hasText: '个人配置已更新' }).waitFor()
    assert.equal(await askModel.inputValue(), 'deepseek-test', '自动保存失败时恢复上次保存的模型')
    await page.unroute(`**${preferencesPath}`, failSave)
    await page.getByLabel('问答备用 1', { exact: true }).selectOption('local-test')
    await askChoices.getByRole('status').filter({ hasText: '模型选择已保存' }).waitFor()
    const persistedPreferences = await (await page.request.get(`http://127.0.0.1:18768${preferencesPath}`)).json()
    assert.deepEqual(persistedPreferences.scenes.ask, ['deepseek-test', 'local-test'])
    await page.getByRole('button', { name: '停用 deepseek-test', exact: true }).click()
    await page.getByRole('button', { name: '启用 deepseek-test', exact: true }).waitFor()
    page.once('dialog', dialog => dialog.accept())
    await deepseekCard.getByRole('button', { name: '测试文本', exact: true }).click()
    await deepseekCard.getByRole('status').filter({ hasText: '模型已停用或未开放所测能力' }).waitFor()
    await page.getByRole('button', { name: '启用 deepseek-test', exact: true }).click()
    await page.getByRole('button', { name: '停用 deepseek-test', exact: true }).waitFor()
    await page.getByRole('button', { name: '停用 deepseek-test', exact: true }).click()
    await page.getByRole('button', { name: '启用 deepseek-test', exact: true }).waitFor()
    page.once('dialog', dialog => dialog.dismiss())
    await page.getByRole('button', { name: '删除 deepseek-test', exact: true }).click()
    assert.equal(await page.getByRole('button', { name: '编辑 deepseek-test', exact: true }).count(), 1)
    page.once('dialog', dialog => dialog.accept())
    await page.getByRole('button', { name: '删除 deepseek-test', exact: true }).click()
    await page.getByRole('button', { name: '编辑 deepseek-test', exact: true }).waitFor({ state: 'detached' })
    assert.equal(await page.getByLabel('问答主模型').inputValue(), 'deepseek-test')
    assert.match(await page.getByLabel('问答主模型').locator('option:checked').textContent(), /已失效/)
    await page.reload()
    await page.getByRole('button', { name: '编辑 local-test', exact: true }).waitFor()
    assert.equal(await page.getByRole('button', { name: '编辑 deepseek-test', exact: true }).count(), 0)
    assert.deepEqual(errors, [])
    console.log('PASS: 真实登录 → 表单保存 → SQLite 写入/读回 → 模型列表及场景选项 → 刷新后仍存在')
  } else {
  await page.waitForFunction(() => document.querySelector('button[disabled]'))
  try {
    await page.getByRole('button', { name: '正在保存模型…', exact: true }).waitFor({ timeout: 2000 })
  } finally { releaseSave() }
  await page.getByRole('status').filter({ hasText: '模型 local-test 已保存' }).waitFor({ timeout: 2000 })
  await page.getByRole('button', { name: '编辑 local-test', exact: true }).waitFor()
  assert.equal(await page.getByLabel('模型标识', { exact: true }).inputValue(), '')
  assert.equal(writes, 1)

  // 浏览器原生校验不应让点击看起来毫无反馈，也不能发送无效请求。
  await save.click()
  await page.getByRole('alert').filter({ hasText: '模型标识' }).waitFor({ timeout: 2000 })
  await page.getByLabel('模型标识', { exact: true }).fill('qwen3:4b')
  await page.getByLabel('厂商模型 ID', { exact: true }).fill('qwen3:4b')
  await save.click()
  await page.getByRole('alert').filter({ hasText: '模型标识' }).waitFor({ timeout: 2000 })
  assert.equal(writes, 1)

  await page.getByRole('button', { name: '编辑 local-test', exact: true }).click()
  rejectSave = true
  await save.click()
  await page.waitForFunction(() => document.querySelector('button[disabled]'))
  releaseSave()
  await page.getByRole('alert').filter({ hasText: '配置已被修改' }).waitFor()
  assert.equal(await page.getByLabel('模型标识', { exact: true }).inputValue(), 'local-test')
  assert.equal(writes, 2)
  assert.deepEqual(errors, [])
  console.log('PASS: 保存中、成功回显、必填/格式校验、失败保留输入')
  }
  }
} finally {
  await browser?.close()
  await server.close()
  await rm(fixture, { recursive: true, force: true })
  if (backend && backend.exitCode === null) {
    const stopped = once(backend, 'exit')
    backend.kill('SIGINT')
    await stopped
  }
}
