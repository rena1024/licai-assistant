import React from 'react'
import { expect,it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { SourcePolicyForm } from '../components/DataCenter'
it('三市场实时价与前复权日K分别配置，同源别名不列为备用',()=>{
 const html=renderToStaticMarkup(<SourcePolicyForm policy={{quotes:{A:['sina','tencent'],HK:['sina','tencent'],US:['sina','tencent']},kline:['eastmoney','tencent'],news:['em']}} onChange={()=>{}} />)
 expect(html).toContain('A 股实时价');expect(html).toContain('HK 股实时价');expect(html).toContain('US 股实时价')
 expect(html).toContain('腾讯独立备用');expect(html).toContain('前复权');expect(html).not.toContain('rt_hk')
})
