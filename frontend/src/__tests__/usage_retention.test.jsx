import React from 'react'
import { expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { BudgetSummary } from '../components/UsagePanel'

it('预算未配置和在途占用分别展示，不把占用当成厂商账单', () => {
  const html = renderToStaticMarkup(<BudgetSummary data={{ user: { day: { limit: null, spent: 100, reserved: 200, available: 0 } } }} />)
  expect(html).toContain('未配置')
  expect(html).toContain('已结算估算')
  expect(html).toContain('占用')
  expect(html).toContain('¥0.000200')
})
