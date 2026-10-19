import React from 'react'
import { expect,it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import DataQuality from '../components/DataQuality'
it('缺价和旧值显示覆盖范围及来源时间',()=>{
 const html=renderToStaticMarkup(<DataQuality holdings={[{stock_code:'600000',shares:100,market_value:null,quote_quality:'missing'},{stock_code:'600001',shares:100,market_value:1000,quote_quality:'stale',source_at:'2026-10-16',source_id:'sina'}]}/> )
 expect(html).toContain('1/2'); expect(html).toContain('缺价'); expect(html).toContain('旧值'); expect(html).toContain('2026-10-16')
})

it('实时推送缺价不能产生零市值和负100%收益',async()=>{
 const { applyHoldingQuote }=await import('../helpers/holdingQuotes')
 const h=applyHoldingQuote({stock_code:'600000',shares:100,cost_price:10}, {price:null,quality:'missing',source_at:null})
 expect(h.market_value).toBeNull();expect(h.unrealized_pnl).toBeNull();expect(h.pnl_pct).toBeNull();expect(h.quote_quality).toBe('missing')
})
