import React from 'react'
import { expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { BudgetSummary, RecentCalls } from '../components/UsagePanel'

it('预算未配置和在途占用分别展示，不把占用当成厂商账单', () => {
  const html = renderToStaticMarkup(<BudgetSummary data={{ user: { day: { limit: null, spent: 100, reserved: 200, available: 0 } } }} />)
  expect(html).toContain('未配置')
  expect(html).toContain('已结算估算')
  expect(html).toContain('占用')
  expect(html).toContain('¥0.000200')
})

it('近期调用默认只展示前20条，超过时才提供展开入口', () => {
  const details = Array.from({ length: 25 }, (_, index) => ({ id: String(index), scene: 'ask', model_id: `model-${index}`, state: 'settled', cost: 0 }))
  const html = renderToStaticMarkup(<RecentCalls details={details} />)
  expect(html.match(/<tr>/g)).toHaveLength(21)
  expect(html).toContain('model-19')
  expect(html).not.toContain('model-20')
  expect(html).toContain('aria-expanded="false"')
  expect(html).toContain('展开全部（共 25 条）')
})

it('近期调用无记录或不超过20条时不显示展开按钮', () => {
  for (const length of [0, 1, 20]) {
    const details = Array.from({ length }, (_, index) => ({ id: String(index), scene: 'ask', model_id: 'model', state: 'settled', cost: 0 }))
    const html = renderToStaticMarkup(<RecentCalls details={details} />)
    expect(html).not.toContain('aria-expanded')
    if (!length) expect(html).toContain('暂无调用明细')
  }
})
