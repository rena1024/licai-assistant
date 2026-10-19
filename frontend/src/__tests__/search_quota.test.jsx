import React from 'react'
import { expect,it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { SearchChannelForm } from '../components/DataCenter'
it('未知额度不显示无限或零，凭证不回显，开放前要求核验免费条件',()=>{
 const html=renderToStaticMarkup(<SearchChannelForm provider="tavily" saved={{key:'must-not-echo',available:null,configured:true,version:1}} onSaved={()=>{}} />)
 expect(html).toContain('未知');expect(html).toContain('未开放');expect(html).toContain('自动付费');expect(html).toContain('凭证仅本应用使用')
 expect(html).not.toContain('must-not-echo');expect(html).toContain('type="password"')
})
