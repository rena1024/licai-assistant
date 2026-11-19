import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { expect, it, vi } from 'vitest'
import UnifiedPortfolio from '../components/UnifiedPortfolio'

const sorting = vi.hoisted(() => ({ key: 'mv' }))
vi.mock('react', async importOriginal => {
  const original = await importOriginal()
  return {
    ...original,
    useState: initial => original.useState(initial === 'mv' ? sorting.key : initial),
  }
})
vi.mock('../components/portfolio/HoldingRow', () => ({
  HoldingRow: ({ row }) => <div data-holding={row.code} />,
}))

const holdings = [
  { stock_code: '600001', stock_name: '合成科技甲', shares: 10, market_value: 120, cost_value: 110, unrealized_pnl: 10, sector: '软件' },
  { stock_code: '600002', stock_name: '合成科技乙', shares: 10, market_value: 80, cost_value: 50, unrealized_pnl: 30, sector: '软件' },
  { stock_code: '600003', stock_name: '合成医药甲', shares: 10, market_value: 160, cost_value: 170, unrealized_pnl: -10, sector: '医药' },
  { stock_code: '600004', stock_name: '合成医药乙', shares: 10, market_value: 60, cost_value: 55, unrealized_pnl: 5, sector: '医药' },
]

it.each([
  ['mv', ['600003', '600004', '600001', '600002']],
  ['pnl', ['600002', '600001', '600004', '600003']],
])('按 %s 对行业汇总及组内持仓降序排列', (key, expected) => {
  sorting.key = key
  const html = renderToStaticMarkup(<UnifiedPortfolio holdings={holdings} />)
  const actual = [...html.matchAll(/data-holding="([^"]+)"/g)].map(match => match[1])
  expect(actual).toEqual(expected)
})
