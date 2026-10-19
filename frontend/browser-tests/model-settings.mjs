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
const settingsLayout = process.argv.includes('--settings-layout')
const fixture = await mkdtemp(`${root}.model-save-test-`)
const fixturePath = `/${basename(fixture)}`
await writeFile(`${fixture}/index.html`, '<!doctype html><html><body><div id="root"></div><script type="module" src="./entry.jsx"></script></body></html>')
await writeFile(`${fixture}/entry.jsx`, `
  import React from 'react';
  import { createRoot } from 'react-dom/client';
  import ModelSettings from '../src/components/ModelSettings.jsx';
  import Settings from '../src/components/Settings.jsx';
  ${settingsLayout ? "import '../src/index.css';" : ''}
  import { setSessionToken } from '../src/hooks/sessionRuntime.js';
  ${realAPI ? `const login = await fetch('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: 'model-tester', password: 'Synthetic-model-test-01!' }) });
  const session = await login.json();
  if (!login.ok) throw new Error('合成账号登录失败：' + JSON.stringify(session));
  setSessionToken(session.csrf_token);` : ''}
  createRoot(document.getElementById('root')).render(${settingsLayout ? '<Settings onClose={() => {}} />' : '<ModelSettings admin />'});
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
  await page.goto(`http://127.0.0.1:18767${fixturePath}/index.html`)
  if (settingsLayout) {
    await page.getByRole('heading', { name: '设置', level: 2, exact: true }).waitFor({ timeout: 3000 })
    const push = page.getByRole('region', { name: '推送设置', exact: true })
    await push.getByRole('heading', { name: '推送设置', level: 3, exact: true }).waitFor()
    await push.getByLabel('飞书 Webhook URL', { exact: true }).waitFor()
    assert.equal(await push.getByRole('button', { name: '保存', exact: true }).count(), 1)
    assert.equal(await push.getByRole('button', { name: '发送测试', exact: true }).count(), 1)
    assert.equal(await push.getByText('用量与预算', { exact: true }).count(), 0)
    await page.getByRole('heading', { name: '用量与预算', level: 3, exact: true }).waitFor()
    await page.getByRole('heading', { name: '我的 AI 设置', level: 3, exact: true }).waitFor()
    await page.getByRole('heading', { name: '账号管理', level: 3, exact: true }).waitFor()
    assert.equal(await page.getByRole('heading', { level: 2 }).count(), 1)
    await page.setViewportSize({ width: 390, height: 844 })
    assert.ok(await push.isVisible())
    await push.scrollIntoViewIfNeeded()
    if (process.env.SETTINGS_SCREENSHOT) await page.screenshot({ path: process.env.SETTINGS_SCREENSHOT, animations: 'disabled' })
    assert.deepEqual(errors, [])
    console.log('PASS: 设置总标题、预算/推送/AI同级、飞书控件归组及移动端呈现')
  } else {
  const save = page.getByRole('button', { name: '保存模型', exact: true })
  await save.waitFor()
  await page.getByLabel('模型标识', { exact: true }).fill('local-test')
  await page.getByLabel('厂商模型 ID', { exact: true }).fill('qwen3:4b')
  await save.click()
  if (realAPI) {
    try { await page.getByRole('status').filter({ hasText: '模型 local-test 已保存' }).waitFor({ timeout: 5000 }) }
    catch { throw new Error(`保存未成功：${(await page.getByRole('alert').allTextContents()).join('; ')}`) }
    await page.getByRole('button', { name: '编辑 local-test', exact: true }).waitFor()
    assert.equal(await page.getByLabel('问答主模型').locator('option[value="local-test"]').count(), 1)
    assert.equal(await page.getByLabel('问答主模型').inputValue(), '')
    const catalog = await (await page.request.get('http://127.0.0.1:18768/api/models')).json()
    assert.equal(catalog.models[0].model, 'qwen3:4b')
    await page.reload()
    await page.getByRole('button', { name: '编辑 local-test', exact: true }).waitFor()
    assert.equal(await page.getByLabel('问答主模型').locator('option[value="local-test"]').count(), 1)
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
