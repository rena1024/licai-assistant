// C 阶段真实浏览器 + 临时 SQLite；所有外部投递为合成发送器。
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { mkdtemp, writeFile, rm } from 'node:fs/promises'
import { basename } from 'node:path'
import { spawn } from 'node:child_process'
import { once } from 'node:events'
import { createServer } from 'vite'
const { chromium } = createRequire(import.meta.url)('playwright')
const root = fileURLToPath(new URL('../', import.meta.url))
const fixture = await mkdtemp(`${root}.c-settings-test-`)
const server = await createServer({ root, server: { host: '127.0.0.1', port: 18777, strictPort: true, proxy: { '/api': { target: 'http://127.0.0.1:18778', changeOrigin: false } } } })
let backend, browser
try {
  await writeFile(`${fixture}/index.html`, '<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module" src="./entry.jsx"></script></body></html>')
  await writeFile(`${fixture}/entry.jsx`, `import React from 'react';import {createRoot} from 'react-dom/client';import AdminPage from '../src/components/AdminPage.jsx';import NotificationSettings from '../src/components/NotificationSettings.jsx';import {SearchPreferences} from '../src/components/DataCenter.jsx';import Header from '../src/components/Header.jsx';import '../src/index.css';import {setSessionToken} from '../src/hooks/sessionRuntime.js';const user=new URLSearchParams(location.search).get('user')==='regular';const r=await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:user?'c-user':'c-admin',password:user?'Synthetic-C-02!':'Synthetic-C-01!'})});const s=await r.json();if(!r.ok)throw Error('合成登录失败');setSessionToken(s.csrf_token);createRoot(document.getElementById('root')).render(<><Header onRefresh={()=>{}} onSettings={()=>{}} onNotifications={()=>{}}/><AdminPage admin={!user} page="admin-data" title="数据与搜索"/><SearchPreferences/><NotificationSettings admin={!user}/></>);`)
  backend = spawn(`${root}../venv/bin/python`, [`${root}browser-tests/c-server.py`], { cwd: `${root}..`, stdio: 'inherit' })
  let ready = false
  for (let i = 0; i < 100; i++) {
    try { ready = (await fetch('http://127.0.0.1:18778/api/health')).ok } catch {}
    if (ready) break
    await new Promise(r => setTimeout(r, 100))
  }
  assert.ok(ready, '隔离服务未启动')
  await server.listen()
  browser = await chromium.launch({ headless: true, channel: 'chrome' })
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } })
  const errors = []
  page.on('pageerror', e => errors.push(e.message))
  const url = `http://127.0.0.1:18777/${basename(fixture)}/index.html`
  await page.goto(url)
  await page.getByRole('heading', { name: '数据与搜索', exact: true }).waitFor()
  await page.getByText('管理员本人预算提醒', { exact: true }).waitFor()
  assert.equal(await page.getByText('普通用户个人提醒', { exact: true }).count(), 0)
  await page.getByRole('button', { name: '标为已读', exact: true }).click()
  await page.getByText('已读', { exact: true }).waitFor()
  await page.getByLabel('配置名称', { exact: true }).fill('合成微信')
  await page.getByLabel('PushPlus Token', { exact: true }).fill('synthetic-token-never-send')
  await page.getByRole('button', { name: '保存渠道', exact: true }).click()
  await page.getByRole('status').filter({ hasText: '已保存；保存不会发送测试，也不会自动启用。' }).waitFor()
  assert.equal(await page.getByRole('button', { name: '发送测试至 合成微信', exact: true }).count(), 0)
  await page.getByRole('button', { name: '启用', exact: true }).click()
  const test = page.getByRole('button', { name: '发送测试至 合成微信', exact: true })
  await test.waitFor()
  page.once('dialog', d => d.accept())
  await test.click()
  await page.getByText('测试已排队，请在消息中心查看渠道发送结果。', { exact: true }).waitFor()
  let accepted = false
  for (let i = 0; i < 50; i++) {
    const result = await (await page.request.get('http://127.0.0.1:18777/api/notifications')).json()
    accepted = result.messages.some(m => m.event === 'test' && m.delivery_state === 'accepted')
    if (accepted) break
    await new Promise(r => setTimeout(r, 100))
  }
  assert.ok(accepted, '合成渠道未接受消息')
  await page.reload()
  await page.getByText('通知测试', { exact: true }).waitFor()
  await page.getByText('渠道已接受 · 渠道接受不代表已读', { exact: true }).waitFor()
  assert.ok(!(await page.content()).includes('synthetic-token-never-send'), '凭证被回显')
  const search = page.getByLabel('允许本人问答使用免费在线搜索', { exact: true })
  await Promise.all([page.waitForResponse(r => r.url().endsWith('/api/search/preferences') && r.request().method() === 'PUT'), search.uncheck()])
  await page.reload()
  await page.getByLabel('允许本人问答使用免费在线搜索', { exact: true }).waitFor()
  assert.equal(await page.getByLabel('允许本人问答使用免费在线搜索', { exact: true }).isChecked(), false)
  await page.getByLabel('A 股实时价', { exact: true }).selectOption('tencent,sina')
  await page.getByRole('button', { name: '保存来源策略', exact: true }).click()
  await page.getByRole('status').filter({ hasText: '来源策略已保存' }).waitFor()
  await page.reload()
  await page.getByLabel('A 股实时价', { exact: true }).waitFor()
  assert.equal(await page.getByLabel('A 股实时价', { exact: true }).inputValue(), 'tencent,sina')
  for (const width of [320, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 })
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), `宽度${width}页面横向溢出`)
  }
  if (process.env.C_SCREENSHOT) await page.screenshot({ path: process.env.C_SCREENSHOT, fullPage: true })
  await page.goto(`${url}?user=regular`)
  await page.getByText('普通用户个人提醒', { exact: true }).waitFor()
  assert.equal(await page.getByRole('heading', { name: '数据与搜索', exact: true }).count(), 0)
  assert.equal(await page.getByText('管理员本人预算提醒', { exact: true }).count(), 0)
  assert.equal(await page.getByRole('button', { name: '发送测试至 合成微信', exact: true }).count(), 0)
  assert.equal((await page.request.get('http://127.0.0.1:18777/api/search/channels')).status(), 403)
  assert.deepEqual(errors, [])
  console.log('C browser: real login/API/SQLite, sources, search switch, private channels, synthetic delivery and responsive layouts passed')
} finally {
  await browser?.close()
  await server.close()
  if (backend && backend.exitCode == null) { backend.kill('SIGTERM'); await once(backend, 'exit') }
  await rm(fixture, { recursive: true, force: true })
}
