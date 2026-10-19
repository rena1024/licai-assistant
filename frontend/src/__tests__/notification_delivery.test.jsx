import React from 'react'
import { expect,it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { MessageList } from '../components/NotificationCenter'
it('未知结果明确不自动重发，手动重发显示当前目标，已读独立',()=>{
 const messages=[{id:'x',title:'个人提醒',body:'详情',delivery_state:'unknown',read_at:null,created_at:1}]
 const html=renderToStaticMarkup(<MessageList messages={messages} activeChannel={{id:'a',name:'当前微信'}} onRead={()=>{}} onResend={()=>{}} />)
 expect(html).toContain('结果未知，未自动重发');expect(html).toContain('重发至 当前微信');expect(html).toContain('未读')
 const noChannel=renderToStaticMarkup(<MessageList messages={messages} onRead={()=>{}} onResend={()=>{}} />)
 expect(noChannel).not.toContain('重发至')
})
