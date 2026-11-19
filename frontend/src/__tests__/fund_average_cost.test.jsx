import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { expect, it } from 'vitest'
import { normalizeAsset } from '../components/portfolio/model'
import { HoldingRow } from '../components/portfolio/HoldingRow'

const asset = {
  id: 7, asset_type: 'FUND', code: '000711', name: '样例基金',
  cost_amount: 300, diluted_cost: 230, shares: 150,
  current_value: 450, pnl: 165, pnl_pct: 55, holding_dividend: 15,
  realized_pnl: 70, cycle_realized: 70, quote: { nav: 3 },
}

it('基金持仓使用平均成本，独立于摊薄成本', () => {
  const row = normalizeAsset(asset)
  expect(row.cost).toBe(300)
  expect(row.pnl).toBe(165)
  expect(normalizeAsset({ ...asset, asset_type: 'CRYPTO' }).cost).toBe(230)
})

it('基金累计总盈亏剔除持有收益已经包含的分红，避免重复计入', () => {
  const html = renderToStaticMarkup(<HoldingRow row={normalizeAsset(asset)}
    totalMv={450} insights={{ overlapRowIds: new Set(), overlaps: [], familyByCode: new Map() }}
    actions={{}} onHover={() => {}} />)
  expect(html).toContain('累计 +220.00')
  expect(html).toContain('持有收益 = 持仓市值')
})
