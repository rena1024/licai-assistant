// 使用合成消息验证选择与批量已读，不连接真实账号。
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { mkdtemp, writeFile, rm } from 'node:fs/promises'
import { basename } from 'node:path'
import { createServer } from 'vite'
const { chromium } = createRequire(import.meta.url)('playwright')
const root = fileURLToPath(new URL('../', import.meta.url))
const fixture = await mkdtemp(`${root}.notification-test-`)
const server = await createServer({ root, server: { host: '127.0.0.1', port: 18789, strictPort: true, hmr: false } })
let browser
try {
  await writeFile(`${fixture}/index.html`, '<!doctype html><html><body><div id="root"></div><script type="module" src="./entry.jsx"></script></body></html>')
  await writeFile(`${fixture}/entry.jsx`, `import React from 'react';import {createRoot} from 'react-dom/client';import NotificationCenter from '../src/components/NotificationCenter.jsx';import '../src/index.css';createRoot(document.getElementById('root')).render(<NotificationCenter/>);`)
  await server.listen()
  browser = await chromium.launch({ headless: true, channel: 'chrome' })
  const page = await browser.newPage()
  const errors = []
  page.on('pageerror', e => errors.push(e.message))
  let messages = Array.from({ length: 60 }, (_, i) => ({ id: String(i), title: `通知 ${i}`, body: '合成消息', created_at: 60 - i, read_at: i === 0 ? 1 : null }))
  const batches = []
  const deletions = []
  let fail = false
  await page.route('**/api/**', async route => {
    if (route.request().method() === 'POST') {
      const { ids } = route.request().postDataJSON()
      if (route.request().url().endsWith('/delete')) {
        deletions.push(ids)
        if (fail) return route.fulfill({ status: 503, json: { detail: '合成删除失败' } })
        messages = messages.filter(m => !ids.includes(m.id))
        return route.fulfill({ json: { ok: true } })
      }
      batches.push(ids)
      if (fail) return route.fulfill({ status: 503, json: { detail: '合成批量失败' } })
      messages = messages.map(m => ids.includes(m.id) ? { ...m, read_at: m.read_at || 123 } : m)
      return route.fulfill({ json: { ok: true } })
    }
    return route.fulfill({ json: { messages } })
  })
  await page.goto(`http://127.0.0.1:18789/${basename(fixture)}/index.html`)
  await page.getByText('通知 0', { exact: true }).waitFor()
  await page.waitForFunction(() => document.querySelectorAll('li').length === 50)
  assert.equal(await page.locator('li').count(), 50)
  assert.equal(await page.getByText('查看更早消息', { exact: true }).count(), 0)
  const all = page.getByRole('checkbox', { name: '全选', exact: true })
  const read = page.getByRole('button', { name: '一键已读', exact: true })
  assert.ok(await read.isDisabled())
  await all.check()
  assert.equal(await page.locator('li input:checked').count(), 50)
  await all.uncheck()
  assert.equal(await page.locator('li input:checked').count(), 0)
  await page.getByRole('checkbox', { name: '选择通知：通知 1', exact: true }).check()
  assert.equal(await all.evaluate(el => el.indeterminate), true)
  fail = true
  await read.click()
  await page.getByRole('alert').filter({ hasText: '合成批量失败' }).waitFor()
  assert.ok(await page.getByRole('checkbox', { name: '选择通知：通知 1', exact: true }).isChecked())
  fail = false
  await read.click()
  await page.locator('li').nth(1).getByText('已读', { exact: true }).waitFor()
  assert.deepEqual(batches.at(-1), ['1'])
  assert.ok(await read.isDisabled())
  await all.check()
  await read.click()
  await page.waitForFunction(() => [...document.querySelectorAll('li')].every(el => !el.textContent.includes('未读')))
  assert.equal(batches.at(-1).length, 48)
  assert.ok(!batches.at(-1).includes('0') && !batches.at(-1).includes('1'))
  assert.equal(messages[50].read_at, null)
  const remove = page.getByRole('button', { name: '一键删除', exact: true })
  assert.ok(await remove.isDisabled())
  await page.getByRole('checkbox', { name: '选择通知：通知 1', exact: true }).check()
  page.once('dialog', dialog => dialog.dismiss())
  await remove.click()
  assert.equal(deletions.length, 0)
  fail = true
  page.once('dialog', dialog => dialog.accept())
  await remove.click()
  await page.getByRole('alert').filter({ hasText: '合成删除失败' }).waitFor()
  assert.ok(await page.getByRole('checkbox', { name: '选择通知：通知 1', exact: true }).isChecked())
  fail = false
  page.once('dialog', dialog => dialog.accept())
  await remove.click()
  await page.getByText('通知 50', { exact: true }).waitFor()
  assert.equal(await page.getByText('通知 1', { exact: true }).count(), 0)
  assert.deepEqual(deletions.at(-1), ['1'])
  assert.equal(await page.locator('li').count(), 50)
  assert.ok(await remove.isDisabled())
  await all.check()
  page.once('dialog', dialog => dialog.accept())
  await remove.click()
  await page.waitForFunction(() => document.querySelectorAll('li').length === 9)
  assert.equal(deletions.at(-1).length, 50)
  for (const width of [320, 768, 1024, 1440]) {
    await page.setViewportSize({ width, height: 900 })
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1))
  }
  assert.deepEqual(errors, [])
  console.log('PASS: 50条上限、全选、批量已读、删除确认/取消/失败/成功、删除后补足列表和响应式布局')
} finally {
  await browser?.close(); await server.close(); await rm(fixture, { recursive: true, force: true })
}
