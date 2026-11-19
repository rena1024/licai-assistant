// 使用合成数据验证列表成本、累计盈亏及编辑窗口口径一致。
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { mkdtemp, writeFile, rm } from 'node:fs/promises'
import { basename } from 'node:path'
import { createServer } from 'vite'

const { chromium } = createRequire(process.env.PLAYWRIGHT_PACKAGE || import.meta.url)('playwright')
const root = fileURLToPath(new URL('../', import.meta.url))
const fixture = await mkdtemp(`${root}.fund-average-test-`)
const server = await createServer({ root, server: { host: '127.0.0.1', port: 18793, strictPort: true } })
let browser
try {
  await writeFile(`${fixture}/index.html`, '<!doctype html><html><body><div id="root"></div><script type="module" src="./entry.jsx"></script></body></html>')
  await writeFile(`${fixture}/entry.jsx`, `
import React from 'react';import {createRoot} from 'react-dom/client';
import {HoldingRow} from '../src/components/portfolio/HoldingRow.jsx';
import {EditAssetRow} from '../src/components/portfolio/forms/EditAssetRow.jsx';
import {normalizeAsset} from '../src/components/portfolio/model.js';import '../src/index.css';
const asset={id:7,asset_type:'FUND',code:'000711',name:'样例基金',cost_amount:300,diluted_cost:230,shares:150,current_value:450,pnl:165,pnl_pct:55,holding_dividend:15,realized_pnl:70,cycle_realized:70,quote:{nav:3}};
function App(){const [editing,setEditing]=React.useState(false);return <><HoldingRow row={normalizeAsset(asset)} totalMv={450} hovered={true} onHover={()=>{}} insights={{overlapRowIds:new Set()}} actions={{onEdit:()=>setEditing(true)}}/>{editing&&<EditAssetRow asset={asset} onCancel={()=>setEditing(false)} onDone={()=>{}}/>}</>};
createRoot(document.getElementById('root')).render(<App/>);`)
  await server.listen()
  browser = await chromium.launch({ headless: true, channel: 'chrome' })
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
  const errors = []
  page.on('pageerror', e => errors.push(e.message))
  await page.route('**/api/**', route => route.fulfill({ json: [] }))
  await page.goto(`http://127.0.0.1:18793/${basename(fixture)}/index.html`)
  await page.getByText('¥300.00', { exact: true }).waitFor()
  await page.getByText('+165.00', { exact: true }).waitFor()
  await page.getByText('+55.00%', { exact: true }).waitFor()
  assert.ok((await page.getByText('+165.00', { exact: true }).getAttribute('title')).includes('¥15.00'))
  await page.getByText('累计 +220.00', { exact: true }).waitFor()
  await page.getByRole('button', { name: '编辑', exact: true }).click()
  await page.getByText('持有成本 ¥/份', { exact: true }).waitFor()
  assert.ok(await page.locator('input').evaluateAll(inputs => inputs.some(input => input.value === '2.0000')))
  assert.ok(await page.locator('input').evaluateAll(inputs => inputs.some(input => input.value === '150')))
  assert.deepEqual(errors, [])
  console.log('PASS: 持有收益165含分红15、收益率55%、累计220不重复分红，平均成本和份额不变')
} finally {
  await browser?.close()
  await server.close()
  await rm(fixture, { recursive: true, force: true })
}
