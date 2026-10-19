const LABELS = { fresh: '交易中报价', last_trade: '最近交易价', delayed: '延迟', stale: '旧值', unknown: '时效未知', missing: '缺价' }

export default function DataQuality({ holdings = [] }) {
  const held = holdings.filter(h => h.shares > 0)
  const covered = held.filter(h => h.market_value != null).length
  const degraded = held.filter(h => !['fresh', 'last_trade'].includes(h.quote_quality))
  if (!held.length) return null
  return <details className="text-xs text-text-muted shrink-0">
    <summary className="cursor-pointer">股票估值覆盖 {covered}/{held.length}{degraded.length ? ' · 统计不完整' : ''}</summary>
    <div className="p-2 space-y-1">
      {held.map(h => <div key={h.stock_code}>{h.stock_name || h.stock_code} · {LABELS[h.quote_quality] || '时效未知'} · {h.source_id || '无来源'} · 数据时间 {h.source_at || '未知'} · 获取时间 {h.fetched_at || '未知'}</div>)}
      <p>缺价不计入估值；旧值与未知时效不能作为完整实时收益依据。</p>
    </div>
  </details>
}
