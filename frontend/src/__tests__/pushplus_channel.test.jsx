import React from 'react'
import { expect,it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { ChannelFields } from '../components/NotificationSettings'
it('凭证使用密码输入并提供渠道字段',()=>{
 const html=renderToStaticMarkup(<ChannelFields kind="pushplus" config={{}} onChange={()=>{}} />)
 expect(html).toContain('type="password"');expect(html).toContain('PushPlus Token')
 const mail=renderToStaticMarkup(<ChannelFields kind="email" config={{}} onChange={()=>{}} />)
 expect(mail).toContain('STARTTLS');expect(mail).toContain('收件地址')
})
