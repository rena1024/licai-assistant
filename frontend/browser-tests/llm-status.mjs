// 合成 API 覆盖各 AI 入口的模型提示，不访问真实模型。
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { mkdtemp, writeFile, rm } from 'node:fs/promises'
import { basename } from 'node:path'
import { createServer } from 'vite'
const { chromium } = createRequire(import.meta.url)('playwright')
const root = fileURLToPath(new URL('../', import.meta.url))
const fixture = await mkdtemp(`${root}.llm-status-test-`)
const cases = {
  review: ['DailyReview', '/api/news/daily-review'],
  trade: ['AITradeReview', '/api/portfolio/trade-review-ai'],
  briefing: ['MorningBriefing', '/api/briefing/refresh', '立即生成'],
  article: ['NewsDetailModal', '/api/news/interpret'],
  market: ['SentimentThermometer', '/api/market/sentiment-ai'],
  sector: ['SectorMatrix', '/api/sector/trend-ai'],
  why: ['SectorOpportunities', '/api/sector/why', '为什么动'],
  rules: ['PromptRulesSection', '/api/settings/rules/mine', '立刻挖一次'],
  scanner: ['Rankings', '/api/market/structure'],
  ask: ['StockAsk', '/api/ask/runs/synthetic-run/events'],
  drawer: ['StockAskModal', '/api/ask/runs/synthetic-run/events'],
}
const imports = Object.values(cases).map(([component]) => component === 'PromptRulesSection'
  ? `import {PromptRulesSection} from '../src/components/settings/PromptRulesSection.jsx';`
  : `import ${component} from '../src/components/${component}.jsx';`).join('\n')
await writeFile(`${fixture}/index.html`, '<!doctype html><html><meta name="viewport" content="width=device-width,initial-scale=1"><body><div id="root"></div><script type="module" src="./entry.jsx"></script></body></html>')
await writeFile(`${fixture}/entry.jsx`, `import React from 'react';import {createRoot} from 'react-dom/client';import '../src/index.css';${imports}
const views={${Object.entries(cases).map(([key, [component]]) => `${key}:${component}`).join(',')}};
const View=views[new URLSearchParams(location.search).get('view')];
createRoot(document.getElementById('root')).render(<View page item={{title:'合成资讯',content:'合成正文'}} stock={{code:'600000',name:'合成股票'}} onClose={()=>{}}/>);`)
const server = await createServer({ root, server: { host: '127.0.0.1', port: 18789, strictPort: true } })
let browser
let releases = []
try {
  await server.listen()
  browser = await chromium.launch({ headless: true, channel: 'chrome' })
  for (const [key, [, endpoint, button]] of Object.entries(cases)) {
    if (process.argv.length > 2 && !process.argv.slice(2).includes(key)) continue
    const page = await browser.newPage()
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    let requestId = ''
    // 即使先显示缓存结果，请求在途时也必须显示模型。
    await page.addInitScript(() => {
      localStorage.setItem('sentimentAi', JSON.stringify({ summary: '上次情绪解读' }))
      localStorage.setItem('sectorMatrix_10', JSON.stringify({ m: { rows: [] }, ai: { summary: '上次板块解读' } }))
    })
    await page.route('**/api/**', async route => {
      const request = route.request(), url = new URL(request.url())
      if (url.pathname === '/api/models/activity') {
        if (key === 'ask' || key === 'drawer') {
          if (url.searchParams.get('task_id') !== 'synthetic-run') return route.fulfill({ json: { calls: [] } })
        } else if (!requestId || url.searchParams.get('request_id') !== requestId) {
          return route.fulfill({ json: { calls: [] } })
        }
        return route.fulfill({ json: { calls: [{ model: 'deepseek-flash', model_id: 'deepseek_flash', local: false, status: 'running' }] } })
      }
      if (url.pathname === endpoint) {
        if (key !== 'ask' && key !== 'drawer') {
          requestId = request.headers()['x-llm-activity']
          assert.match(requestId, /^[a-f0-9]{32}$/)
        }
        await new Promise(resolve => releases.push(resolve))
        return route.fulfill({ json: {} }).catch(() => {})
      }
      let data = {}
      if (url.pathname === '/api/portfolio') data = []
      if (url.pathname === '/api/briefing') data = { briefings: [] }
      if (url.pathname === '/api/market/sentiment') data = { n_zt: 1, mood: '中性' }
      if (url.pathname === '/api/sector/matrix') data = { rows: [] }
      if (url.pathname === '/api/sector/scan') data = { sectors: [{ name: '合成板块', change_5d: 1, change_1d: 1 }], total: 1 }
      if (url.pathname === '/api/settings/rules') data = { rules: [] }
      if (url.pathname === '/api/ask/runs' && request.method() === 'POST') data = { run_id: 'synthetic-run', session_id: 'synthetic-session' }
      if (url.pathname === '/api/ask/runs' && request.method() === 'GET') data = { runs: [] }
      return route.fulfill({ json: data })
    })
    await page.goto(`http://127.0.0.1:18789/${basename(fixture)}/index.html?view=${key}${key === 'scanner' ? '#rankings?t=structure' : ''}`)
    if (button) await page.getByRole('button', { name: button, exact: true }).click()
    if (key === 'ask' || key === 'drawer') {
      await page.getByRole('textbox').fill('合成问题')
      await page.getByRole('button', { name: '问', exact: true }).click()
    }
    try {
      await page.getByRole('status').filter({ hasText: '正在调用 deepseek-flash（deepseek_flash）（云端）' }).waitFor({ timeout: 5000 })
      assert.deepEqual(errors, [], `${key} 不应出现浏览器错误`)
      if (key === 'article') await page.screenshot({ path: '/tmp/licai-llm-status.png' })
      console.log(`PASS: ${key} 显示本次调用的实际模型`)
    } catch (error) { throw new Error(`${key}: ${error.message}; browser=${errors.join('; ')}`) }
    finally { releases.splice(0).forEach(resolve => resolve()); await page.close() }
  }
} finally {
  releases.splice(0).forEach(resolve => resolve())
  await browser?.close(); await server.close(); await rm(fixture, { recursive: true, force: true })
}
