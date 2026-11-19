// 使用真实键盘输入检查原生日期控件，不提交真实交易。
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { mkdtemp, writeFile, rm } from 'node:fs/promises'
import { basename } from 'node:path'
import { createServer } from 'vite'

const { chromium } = createRequire(process.env.PLAYWRIGHT_PACKAGE || import.meta.url)('playwright')
const root = fileURLToPath(new URL('../', import.meta.url))
const fixture = await mkdtemp(`${root}.lot-date-test-`)
const server = await createServer({ root, server: { host: '127.0.0.1', port: 18792, strictPort: true } })
let browser
try {
  await writeFile(`${fixture}/index.html`, '<!doctype html><html lang="zh-CN"><body><div id="root"></div><script type="module" src="./entry.jsx"></script></body></html>')
  await writeFile(`${fixture}/entry.jsx`, `import React from 'react';import {createRoot} from 'react-dom/client';import {AddLotRow,ReduceLotRow} from '../src/components/portfolio/forms/lots.jsx';import '../src/index.css';const Form=location.search.includes('reduce')?ReduceLotRow:AddLotRow;createRoot(document.getElementById('root')).render(<Form asset={{id:7,asset_type:'FUND',code:'000711',name:'合成基金',cost_amount:1000,shares:1000}} onDone={()=>{}} onCancel={()=>{}}/>);`)
  await server.listen()
  browser = await chromium.launch({ headless: true, channel: 'chrome' })
  const page = await browser.newPage({ locale: 'zh-CN' })
  const errors = []
  page.on('pageerror', e => errors.push(e.message))
  for (const mode of ['add', 'reduce']) {
    await page.goto(`http://127.0.0.1:18792/${basename(fixture)}/index.html?${mode}`)
    const input = page.locator('input[type="date"]')
    await input.fill('2026-11-18')
    await input.focus()
    assert.equal(await input.inputValue(), '2026-11-18')
    // 找到年份段，避免依赖操作系统日期字段的排列顺序。
    for (let segment = 0; segment < 3; segment++) {
      await input.press('ArrowUp')
      await page.waitForFunction(() => document.querySelector('input[type="date"]').value !== '2026-11-18')
      const value = await input.inputValue()
      await input.press('ArrowDown')
      await page.waitForFunction(() => document.querySelector('input[type="date"]').value === '2026-11-18')
      if (value.startsWith('2027-')) break
      await input.press('ArrowRight')
      assert.ok(segment < 2, '应能定位年份段')
    }
    await page.keyboard.type('2021')
    await page.keyboard.type('05')
    const value = await input.inputValue()
    console.log(`${mode}: 输入年份2021再输入月份05 → ${value}`)
    assert.ok(value.startsWith('2021-'), '年份输入四位后应切换到下一段，不能继续追加第五、第六位')
    assert.equal(value.slice(5, 7), '05', '年份四位后输入05应落到月份')
    await page.keyboard.type('20')
    assert.equal(await input.inputValue(), '2021-05-20', '应能连续输入完整历史日期')
  }
  assert.deepEqual(errors, [])
  console.log('PASS: 加仓和减仓的年份四位后自动切换月份')
} finally {
  await browser?.close()
  await server.close()
  await rm(fixture, { recursive: true, force: true })
}
