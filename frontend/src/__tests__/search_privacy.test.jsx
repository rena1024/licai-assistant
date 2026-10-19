import React from 'react'
import {expect,it} from 'vitest'
import {renderToStaticMarkup} from 'react-dom/server'
import { SearchDisclosure, SourcePolicyForm } from '../components/DataCenter'
it('本地模型搜索提示查询词外发且开关独立',()=>{
 const html=renderToStaticMarkup(<SearchDisclosure enabled={true} onChange={()=>{}} />)
 expect(html).toContain('查询词会离开本机');expect(html).toContain('独立于模型')
})
it('行情和K线分开设置',()=>{
 const html=renderToStaticMarkup(<SourcePolicyForm policy={{quotes:{A:['sina','tencent'],HK:['sina','tencent'],US:['sina','tencent']},kline:['eastmoney','tencent'],news:['em']}} onChange={()=>{}} />)
 expect(html).toContain('实时价');expect(html).toContain('前复权');expect(html).toContain('资讯聚合')
})
