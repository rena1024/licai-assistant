import React from 'react'
import { expect,it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { MessageList } from '../components/NotificationCenter'
it('渠道已接受与已读状态独立显示',()=>{
 const html=renderToStaticMarkup(<MessageList messages={[{id:'one',title:'预算提醒',body:'请查看用量',delivery_state:'accepted',read_at:null,created_at:1}]} onRead={()=>{}} />)
 expect(html).toContain('渠道已接受');expect(html).toContain('未读');expect(html).toContain('标为已读')
})
