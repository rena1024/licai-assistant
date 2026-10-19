// 推送沿用服务端缺价合同，避免 null 在 JavaScript 算术中隐式变成 0。
export function applyHoldingQuote(h, q) {
  if (!q) return h
  const currentPrice = q.price
  const metadata = { quote_quality: q.quality || 'unknown', source_id: q.source_id, source_at: q.source_at, fetched_at: q.fetched_at, stale_reason: q.stale_reason, market_state: q.market_state }
  if (!(currentPrice > 0) || !Number.isFinite(currentPrice)) {
    return { ...h, ...metadata, current_price: null, market_value: null, original_market_value: null, unrealized_pnl: null, pnl_pct: null, price_change_pct: null }
  }
  const fxRate = q.fx_rate || h.fx_rate || 1
  const originalCostValue = h.cost_price * h.shares
  const originalMarketValue = currentPrice * h.shares
  const pnl = (originalMarketValue - originalCostValue) * fxRate
  const pnlPct = h.cost_price > 0 ? (currentPrice - h.cost_price) / h.cost_price * 100 : null
  return {
    ...h, ...metadata, current_price: currentPrice, fx_rate: fxRate,
    fx_time: q.fx_time || h.fx_time || '', fx_source: q.fx_source || h.fx_source || '',
    price_change_pct: q.change_pct,
    today_valid: q.today_valid !== undefined ? q.today_valid : h.today_valid,
    unrealized_pnl: Math.round(pnl * 100) / 100,
    pnl_pct: pnlPct == null ? null : Math.round(pnlPct * 100) / 100,
    original_cost_value: Math.round(originalCostValue * 100) / 100,
    original_market_value: Math.round(originalMarketValue * 100) / 100,
    cost_value: Math.round(originalCostValue * fxRate * 100) / 100,
    market_value: Math.round(originalMarketValue * fxRate * 100) / 100,
  }
}
