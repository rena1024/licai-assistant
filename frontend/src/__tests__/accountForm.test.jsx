import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { AccountForm, PasswordHint } from '../components/AccountGate.jsx'

const render = (component, props) => renderToStaticMarkup(createElement(component, props))

describe('密码规则和可取消的账号表单', () => {
  it('初始化和改密表单允许4位密码', () => {
    for (const mode of ['bootstrap', 'password']) {
      const html = render(AccountForm, { mode })
      expect(html).toContain('minLength="4"')
      expect(html).not.toContain('minLength="12"')
      expect(html).toContain('密码长度为 4～128 个字符')
    }
  })

  it('4至7字符显示弱密码提示，8字符及空值不显示', () => {
    for (const password of ['1234', '1234567']) {
      expect(render(PasswordHint, { password })).toContain('密码强度太弱')
    }
    for (const password of ['', '12345678']) {
      expect(render(PasswordHint, { password })).not.toContain('密码强度太弱')
    }
  })

  it('切换表单提供取消切换并说明当前账号仍然登录', () => {
    const html = render(AccountForm, { mode: 'switch', currentUsername: 'alice', onCancel: () => {} })
    expect(html).toContain('切换账号')
    expect(html).toContain('取消切换')
    expect(html).toContain('alice')
    expect(html).toContain('仍保持登录')
  })
})
