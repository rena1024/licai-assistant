// 合成响应验证早盘简报空结果与失败提示，不调用真实模型或修改持仓。
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { mkdtemp, writeFile, rm } from 'node:fs/promises'
import { basename } from 'node:path'
import { createServer } from 'vite'
const { chromium } = createRequire(import.meta.url)('playwright')
const root = fileURLToPath(new URL('../', import.meta.url))
const fixture = await mkdtemp(`${root}.briefing-feedback-`)
const server = await createServer({ root, server: { host: '127.0.0.1', port: 18790, strictPort: true } })
let browser
try {
  await writeFile(`${fixture}/index.html`, '<!doctype html><html><meta name="viewport" content="width=device-width,initial-scale=1"><body><div id="root"></div><script type="module" src="./entry.jsx"></script></body></html>')
  await writeFile(`${fixture}/entry.jsx`, `import React from 'react';import {createRoot} from 'react-dom/client';import MorningBriefing from '../src/components/MorningBriefing.jsx';import '../src/index.css';createRoot(document.getElementById('root')).render(<MorningBriefing/>);`)
  await server.listen()
  browser = await chromium.launch({ headless: true, channel: 'chrome' })
  const page = await browser.newPage()
  const errors = []
  page.on('pageerror', e => errors.push(e.message))
  let mode = 'empty', briefings = [], release, posts = 0
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url())
    if (url.pathname === '/api/models/activity') return route.fulfill({ json: { calls: [] } })
    if (route.request().method() === 'POST') {
      posts++
      if (mode === 'error') return route.fulfill({ status: 503, json: { detail: '模型服务暂不可用' } })
      if (mode === 'pending') await new Promise(resolve => { release = resolve })
      if (mode === 'success') briefings = [{ stock_code: '600000', stock_name: '合成持仓', summary: '合成早盘简报', signal: '中性', confidence: 'low' }]
      return route.fulfill({ json: { date: '2026-10-28', count: briefings.length, briefings } })
    }
    return route.fulfill({ json: { date: '2026-10-28', is_today: true, briefings } })
  })
  await page.goto(`http://127.0.0.1:18790/${basename(fixture)}/index.html`)
  const generate = page.getByRole('button', { name: '立即生成', exact: true })
  await generate.click()
  await page.getByRole('status').filter({ hasText: '当前没有可生成简报的有效持仓' }).waitFor({ timeout: 2000 })
  mode = 'error'
  await generate.click()
  await page.getByRole('alert').filter({ hasText: '模型服务暂不可用' }).waitFor()
  mode = 'pending'
  await generate.click()
  await page.getByRole('button', { name: '生成中…', exact: true }).waitFor()
  assert.ok(await page.getByRole('button', { name: '生成中…', exact: true }).isDisabled())
  assert.equal(posts, 3)
  await page.getByRole('status').filter({ hasText: '正在准备 AI 请求' }).waitFor()
  mode = 'success'
  release()
  await page.getByText('合成早盘简报', { exact: true }).waitFor()
  assert.equal(await page.getByRole('alert').count(), 0)
  await page.getByRole('status').filter({ hasText: '早盘简报已更新' }).waitFor()
  mode = 'error'
  await page.getByRole('button', { name: '重新生成', exact: true }).click()
  await page.getByRole('alert').filter({ hasText: '模型服务暂不可用' }).waitFor()
  await page.getByText('合成早盘简报', { exact: true }).waitFor()
  for (const width of [320, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 })
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1))
  }
  assert.deepEqual(errors, [])
  console.log('PASS: 无持仓提示、生成失败、等待反馈、防重复点击、成功反馈及已有简报保留')
} finally {
  await browser?.close(); await server.close(); await rm(fixture, { recursive: true, force: true })
}
