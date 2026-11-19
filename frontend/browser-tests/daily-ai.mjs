// 合成接口验证切页复用、手动重新生成和跨日，不连接真实模型。
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { mkdtemp, writeFile, rm } from 'node:fs/promises'
import { basename } from 'node:path'
import { createServer } from 'vite'
const { chromium } = createRequire(import.meta.url)('playwright')
const root = fileURLToPath(new URL('../', import.meta.url))
const fixture = await mkdtemp(`${root}.daily-ai-test-`)
let browser, server
try {
  await writeFile(`${fixture}/index.html`, '<!doctype html><html><body><div id="root"></div><script type="module" src="./entry.jsx"></script></body></html>')
  await writeFile(`${fixture}/entry.jsx`, `import React,{useState} from 'react';import{createRoot}from'react-dom/client';
import Sentiment from '../src/components/SentimentThermometer.jsx';import Sector from '../src/components/SectorMatrix.jsx';
import News from '../src/components/PortfolioNews.jsx';import Daily from '../src/components/DailyReview.jsx';import Trade from '../src/components/AITradeReview.jsx';import Rankings from '../src/components/Rankings.jsx';import '../src/index.css';
window.location.hash='#rankings?t=structure&p=week';Date.now=()=>Date.parse('2026-11-19T03:00:00Z');
const screens={情绪:Sentiment,板块:Sector,资讯:News,归因:Daily,交易:Trade,结构:Rankings};function Harness(){const[tab,setTab]=useState('情绪');const Screen=screens[tab];return <><nav>{[...Object.keys(screens),'切走'].map(x=><button key={x} onClick={()=>setTab(x)}>{x}</button>)}</nav>{Screen&&<Screen key={tab}/>}</>};createRoot(document.getElementById('root')).render(<Harness/>);`)
  server = await createServer({ root, server: { host: '127.0.0.1', port: 18791, strictPort: true, hmr: false } })
  await server.listen()
  browser = await chromium.launch({ headless: true, channel: 'chrome' })
  const page = await browser.newPage()
  const navigation = page.locator('nav').first()
  const errors = []
  page.on('pageerror', e => errors.push(e.message))
  const paths = new Map([
    ['/api/market/sentiment-ai', '情绪'], ['/api/sector/trend-ai', '板块'],
    ['/api/news/digest', '资讯'], ['/api/news/daily-review', '归因'],
    ['/api/portfolio/trade-review-ai', '交易'],
    ['/api/market/structure', '结构'],
  ])
  const requests = new Map(), snapshots = new Map()
  let failPath = ''
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url()), path = url.pathname
    if (paths.has(path)) {
      requests.set(path, (requests.get(path) || 0) + 1)
      if (path === failPath) return route.fulfill({ status: 503, json: { detail: '合成失败' } })
      const old = snapshots.get(path)
      const version = old ? old.version + (url.searchParams.has('force') ? 1 : 0) : 1
      const summary = `${paths.get(path)}摘要${version}`
      const data = { version, summary, highlights: [{ title: summary, level: 'watch' }],
        generated_at: '2026-11-19 11:00:00', date: '2026-11-19', period: 'week', empty: false,
        holdings: [], global: [], tomorrow: [], good: [], discipline: [], trends: [], points: [],
        groups: [{ 行业: summary, n: 0, rows: [] }] }
      snapshots.set(path, data)
      return route.fulfill({ json: data })
    }
    if (path === '/api/market/sentiment') return route.fulfill({ json: { n_zt: 1, mood: '数据不足', ladder: [], hot_sectors: [] } })
    if (path === '/api/sector/matrix') return route.fulfill({ json: { days: 10, dates: [], rows: [{ name: '合成行业', today_pct: 1, cum_pct: 1, net_inflow: 0, daily: [] }] } })
    return route.fulfill({ json: { items: [], calls: [], series: [] } })
  })
  await page.goto(`http://127.0.0.1:18791/${basename(fixture)}/index.html`)
  for (const [path, name] of paths) {
    await navigation.getByRole('button', { name, exact: true }).click()
    await page.getByText(`${name}摘要1`).first().waitFor()
    await navigation.getByRole('button', { name: '切走', exact: true }).click()
    await navigation.getByRole('button', { name, exact: true }).click()
    await page.getByText(`${name}摘要1`).first().waitFor()
    assert.equal(requests.get(path), 1, `${name}切页不得重新提交`)
    await page.getByRole('button', { name: /重新生成/ }).first().click()
    await page.getByText(`${name}摘要2`).first().waitFor()
    assert.equal(requests.get(path), 2, `${name}手动重新生成应提交一次`)
  }
  // 不卸载榜单，跨日只切换内部页签，也必须重新校验当天结果。
  await page.evaluate(() => { Date.now = () => Date.parse('2026-11-19T16:00:00Z') })
  await page.getByRole('button', { name: '涨幅', exact: true }).click()
  const structureNextDayResponse = page.waitForResponse(r => r.url().includes('/api/market/structure'))
  await page.getByRole('button', { name: '蓄势/强势', exact: true }).click()
  await structureNextDayResponse
  assert.equal(requests.get('/api/market/structure'), 3, '结构页内部切换也校验跨日')
  failPath = '/api/market/structure'
  await page.getByRole('button', { name: /重新生成/ }).first().click()
  await page.getByRole('button', { name: '重试', exact: true }).waitFor()
  failPath = ''
  await page.getByRole('button', { name: '重试', exact: true }).click()
  await page.getByText('结构摘要3').first().waitFor()
  assert.equal(requests.get('/api/market/structure'), 5, '结构页错误重试必须强制重新提交')
  await page.evaluate(() => { Date.now = () => Date.parse('2026-11-19T03:00:00Z') })
  await navigation.getByRole('button', { name: '资讯', exact: true }).click()
  failPath = '/api/news/digest'
  await page.getByRole('button', { name: /重新生成/ }).first().click()
  await page.getByRole('alert').filter({ hasText: '合成失败' }).waitFor()
  await navigation.getByRole('button', { name: '切走', exact: true }).click()
  await navigation.getByRole('button', { name: '资讯', exact: true }).click()
  await page.getByText('资讯摘要2', { exact: true }).first().waitFor()
  assert.equal(requests.get('/api/news/digest'), 3, '手动失败后切回仍复用成功结果')
  failPath = ''
  await page.evaluate(() => { Date.now = () => Date.parse('2026-11-19T06:00:00Z') })
  await navigation.getByRole('button', { name: '切走', exact: true }).click()
  await navigation.getByRole('button', { name: '板块', exact: true }).click()
  await page.getByText('板块摘要2', { exact: true }).first().waitFor()
  assert.equal(requests.get('/api/sector/trend-ai'), 2, '超过半小时仍不重新提交')
  await page.evaluate(() => { Date.now = () => Date.parse('2026-11-19T16:00:00Z') })
  await navigation.getByRole('button', { name: '切走', exact: true }).click()
  const nextDayResponse = page.waitForResponse(r => r.url().includes('/api/sector/trend-ai'))
  await navigation.getByRole('button', { name: '板块', exact: true }).click()
  await nextDayResponse
  assert.equal(requests.get('/api/sector/trend-ai'), 3, '北京时间次日首次访问重新提交')
  for (const width of [320, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 })
    // 表格保留自己的横向滚动，不撑开整页。
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1))
  }
  assert.deepEqual(errors, [])
  console.log('PASS: 六项分析切页复用、手动生成、失败保留、半小时后复用及北京时间跨日验证通过')
} finally {
  await browser?.close()
  await server?.close()
  await rm(fixture, { recursive: true, force: true })
}
