// 仅使用合成流水，验证补录交互，不修改真实基金数据。
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { mkdtemp, writeFile, rm } from 'node:fs/promises'
import { basename } from 'node:path'
import { createServer } from 'vite'

const { chromium } = createRequire(process.env.PLAYWRIGHT_PACKAGE || import.meta.url)('playwright')
const root = fileURLToPath(new URL('../', import.meta.url))
const fixture = await mkdtemp(`${root}.fund-dividend-test-`)
const server = await createServer({ root, server: { host: '127.0.0.1', port: 18791, strictPort: true } })
let browser
try {
  await writeFile(`${fixture}/index.html`, '<!doctype html><html><body><div id="root"></div><script type="module" src="./entry.jsx"></script></body></html>')
  await writeFile(`${fixture}/entry.jsx`, `import React from 'react';import {createRoot} from 'react-dom/client';import {AssetActionsModal} from '../src/components/portfolio/actions.jsx';import '../src/index.css';function App(){const [changes,setChanges]=React.useState(0);return <><output aria-label="更新次数">{changes}</output><AssetActionsModal asset={{id:7,name:'合成基金',asset_type:'FUND'}} onClose={()=>{}} onChanged={()=>setChanges(n=>n+1)}/></>}createRoot(document.getElementById('root')).render(<App/>);`)
  await server.listen()
  browser = await chromium.launch({ headless: true, channel: 'chrome' })
  const page = await browser.newPage()
  page.setDefaultTimeout(5000)
  const errors = []
  page.on('pageerror', e => errors.push(e.message))
  const rows = []
  const requests = []
  let fail = false
  await page.route('**/api/**', async route => {
    if (route.request().method() === 'POST') {
      assert.ok(route.request().url().endsWith('/api/assets/7/dividends'))
      const data = route.request().postDataJSON()
      requests.push(data)
      await new Promise(resolve => setTimeout(resolve, 100))
      if (fail) return route.fulfill({ status: 400, json: { detail: '合成录入失败' } })
      rows.push({ ...data, id: rows.length + 1, action_type: 'DIVIDEND', status: 'confirmed' })
      return route.fulfill({ json: { message: '现金分红已录入' } })
    }
    return route.fulfill({ json: { actions: rows, state: { cost_amount: 1000, shares: 1000, realized_pnl: rows.reduce((sum, r) => sum + r.amount, 0) } } })
  })
  await page.goto(`http://127.0.0.1:18791/${basename(fixture)}/index.html`)
  await page.getByRole('button', { name: '补录现金分红', exact: true }).click()
  const date = page.getByLabel('到账日期', { exact: true })
  const amount = page.getByLabel('到账金额（元）', { exact: true })
  await date.fill('2999-01-01')
  await amount.fill('0')
  await page.getByRole('button', { name: '保存', exact: true }).click()
  assert.equal(requests.length, 0)
  assert.equal(await date.evaluate(el => el.validity.rangeOverflow), true)
  assert.equal(await amount.evaluate(el => el.validity.rangeUnderflow), true)
  await date.fill('2021-05-20')
  await amount.fill('123.45')
  await page.getByLabel('备注（可选）', { exact: true }).fill('历史到账')
  fail = true
  await page.getByRole('button', { name: '保存', exact: true }).click()
  await page.getByRole('alert').filter({ hasText: '合成录入失败' }).waitFor()
  assert.equal(await amount.inputValue(), '123.45')
  assert.equal(await date.inputValue(), '2021-05-20')
  fail = false
  await page.getByRole('button', { name: '保存并继续', exact: true }).click()
  await page.getByRole('status').filter({ hasText: '现金分红已录入' }).waitFor()
  assert.equal(await amount.inputValue(), '')
  assert.equal(await date.inputValue(), '2021-05-20')
  assert.deepEqual(requests.at(-1), { amount: 123.45, trade_date: '2021-05-20', note: '历史到账' })
  assert.equal(await page.getByLabel('更新次数').textContent(), '1')
  await amount.fill('20')
  const save = page.locator('button[value="save"]')
  await save.click()
  assert.ok(await save.isDisabled())
  await date.waitFor({ state: 'detached' })
  assert.equal(rows.length, 2)
  assert.equal(await page.getByLabel('更新次数').textContent(), '2')
  await page.getByRole('button', { name: '补录现金分红', exact: true }).click()
  await page.getByRole('button', { name: '取消', exact: true }).click()
  assert.equal(requests.length, 3)
  for (const width of [320, 768, 1024, 1440]) {
    await page.setViewportSize({ width, height: 900 })
    await page.getByRole('button', { name: '补录现金分红', exact: true }).click()
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1))
    assert.ok(await page.getByRole('button', { name: '保存并继续', exact: true }).isVisible())
    if (process.env.DIVIDEND_SCREENSHOT_DIR) {
      await page.screenshot({ path: `${process.env.DIVIDEND_SCREENSHOT_DIR}/fund-dividend-${width}.png`, fullPage: true })
    }
    await page.getByRole('button', { name: '取消', exact: true }).click()
  }
  assert.deepEqual(errors, [])
  console.log('PASS: 历史日期、失败保留输入、连续录入、保存刷新、防重复提交、取消及响应式布局')
} finally {
  await browser?.close()
  await server.close()
  await rm(fixture, { recursive: true, force: true })
}
