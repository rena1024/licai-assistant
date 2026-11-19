// 隔离资讯摘要的交互回归，所有资讯与模型失败均由合成响应提供。
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { mkdtemp, writeFile, rm } from 'node:fs/promises'
import { basename } from 'node:path'
import { createServer } from 'vite'
const { chromium } = createRequire(import.meta.url)('playwright')
const root = fileURLToPath(new URL('../', import.meta.url))
const fixture = await mkdtemp(`${root}.digest-test-`)
const server = await createServer({ root, server: { host: '127.0.0.1', port: 18787, strictPort: true } })
let browser
try {
  await writeFile(`${fixture}/index.html`, '<!doctype html><html><body><div id="root"></div><script type="module" src="./entry.jsx"></script></body></html>')
  await writeFile(`${fixture}/entry.jsx`, `import React from 'react';import {createRoot} from 'react-dom/client';import PortfolioNews from '../src/components/PortfolioNews.jsx';import '../src/index.css';createRoot(document.getElementById('root')).render(<PortfolioNews/>);`)
  await server.listen()
  browser = await chromium.launch({ headless: true, channel: 'chrome' })
  const page = await browser.newPage()
  const errors = []
  page.on('pageerror', e => errors.push(e.message))
  let mode = 'cached', release, started
  let pending
  let activityCalls = []
  let activityAvailable = true
  let digestRequestId = ''
  const snapshot = { summary: '上次已生成摘要', highlights: [{ title: '上次资讯要点', level: 'watch' }], generated_at: '2026-10-20 10:00:00', input_count: 5 }
  await page.route('**/api/**', async route => {
    if (route.request().url().includes('/api/models/activity')) {
      if (!activityAvailable) return route.fulfill({ status: 503, json: { detail: '合成状态读取失败' } })
      if (digestRequestId) assert.equal(new URL(route.request().url()).searchParams.get('request_id'), digestRequestId)
      return route.fulfill({ json: { calls: activityCalls } })
    }
    if (!route.request().url().includes('/api/news/digest')) return route.fulfill({ json: { items: [] } })
    digestRequestId = route.request().headers()['x-llm-activity']
    assert.match(digestRequestId, /^[a-f0-9]{32}$/)
    if (mode === 'pending') {
      await new Promise(resolve => { release = resolve; started() })
      return route.fulfill({ status: 503, json: { detail: { message: '本地模型不可用，云端调用已暂停' } } })
    }
    if (mode === 'failure') return route.fulfill({ status: 503, json: { detail: { message: '本地模型不可用，云端调用已暂停' } } })
    if (mode === 'body-error') return route.fulfill({ json: { error: '所选模型不可用：provider_transport_error', highlights: [] } })
    if (mode === 'empty') return route.fulfill({ json: { summary: '', highlights: [], input_count: 0 } })
    return route.fulfill({ json: mode === 'success' ? { ...snapshot, summary: '本次新生成摘要', highlights: [{ title: '本次新要点', level: 'watch' }] } : snapshot })
  })
  await page.goto(`http://127.0.0.1:18787/${basename(fixture)}/index.html`)
  await page.getByText('上次资讯要点', { exact: true }).waitFor()
  const regenerate = page.getByRole('button', { name: /重算|重新生成/ })
  mode = 'failure'
  await regenerate.click()
  await page.getByRole('alert').filter({ hasText: '本地模型不可用，云端调用已暂停' }).waitFor({ timeout: 2000 })
  await page.getByText('上次资讯要点', { exact: true }).waitFor()
  await page.getByRole('button', { name: '收起', exact: true }).click()
  await regenerate.click()
  await page.getByRole('alert').filter({ hasText: '本地模型不可用' }).waitFor()
  mode = 'pending'
  activityCalls = [{ model_id: 'deepseek_flash', model: 'deepseek-flash', local: false, status: 'running' }]
  pending = new Promise(resolve => { started = resolve })
  await regenerate.click(); await pending
  assert.ok(await regenerate.isDisabled())
  await page.getByRole('status').filter({ hasText: '正在重新生成' }).waitFor()
  await page.getByRole('status').filter({ hasText: '正在调用 deepseek-flash（deepseek_flash）（云端）' }).waitFor()
  activityCalls = [{ model_id: 'local_ollama', model: 'qwen3:8b', local: true, status: 'queued' }]
  await page.getByRole('status').filter({ hasText: '正在等待 qwen3:8b（local_ollama）（本地排队）' }).waitFor()
  activityCalls = [{ model_id: 'local_ollama', model: 'qwen3:8b', local: true, status: 'running' }]
  await page.getByRole('status').filter({ hasText: '正在调用 qwen3:8b（local_ollama）（本地）' }).waitFor()
  activityAvailable = false
  await page.getByRole('status').filter({ hasText: '模型状态暂不可读' }).waitFor()
  activityAvailable = true
  await page.getByRole('status').filter({ hasText: '正在调用 qwen3:8b（local_ollama）（本地）' }).waitFor()
  release()
  await page.getByRole('alert').filter({ hasText: '本地模型不可用' }).waitFor()
  mode = 'body-error'
  await regenerate.click()
  await page.getByRole('alert').filter({ hasText: '模型服务连接失败' }).waitFor()
  await page.getByRole('button', { name: '展开', exact: true }).click()
  await page.getByText('上次资讯要点', { exact: true }).waitFor()
  mode = 'success'
  await regenerate.click()
  await page.getByText('本次新要点', { exact: true }).waitFor()
  assert.equal(await page.getByRole('alert').count(), 0)
  await page.getByRole('status').filter({ hasText: 'AI 摘要已更新' }).waitFor()
  mode = 'empty'
  await regenerate.click()
  await page.getByRole('status').filter({ hasText: '暂无可用资讯' }).waitFor()
  await page.getByText('本次新要点', { exact: true }).waitFor()
  for (const width of [320, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 })
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1))
  }
  assert.deepEqual(errors, [])
  console.log('PASS: 旧摘要和收起状态均显示失败；处理中反馈、HTTP/正文错误、恢复成功和无素材路径通过')
} finally {
  await browser?.close(); await server.close(); await rm(fixture, { recursive: true, force: true })
}
