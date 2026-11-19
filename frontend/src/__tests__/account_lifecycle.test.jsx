import React from 'react'
import { describe, it, expect } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { UserList } from '../components/AdminUsers'

describe('账号管理', () => {
  it('唯一管理员没有停用与重置入口，普通账号展示对应状态', () => {
    const html = renderToStaticMarkup(<UserList users={[
      { id: 1, username: 'admin', role: 'admin', active: true },
      { id: 2, username: 'alice', role: 'user', active: false },
    ]} busy={false} onActive={() => {}} onReset={() => {}} />)
    expect(html).toContain('唯一管理员')
    expect(html.match(/>重置密码<\/button>/g)).toHaveLength(1)
    expect(html).toContain('aria-label="重置密码 alice"')
    expect(html).toContain('启用 alice')
    expect(html).not.toContain('停用 admin')
    expect(html).not.toContain('删除 admin')
    expect(html).toContain('aria-label="删除 alice"')
  })
})
