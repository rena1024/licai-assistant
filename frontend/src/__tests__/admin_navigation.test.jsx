import React from 'react'
import { expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import Sidebar, { NAV } from '../components/Sidebar'
import AdminPage from '../components/AdminPage'
import ModelSettings from '../components/ModelSettings'
import NotificationSettings from '../components/NotificationSettings'

const labels = ['账号管理', '模型服务', '用量与预算', '数据与搜索', '系统配置', '备份与维护']

it('管理员侧边栏有独立管理分组及六个页面入口', () => {
  const html = renderToStaticMarkup(<Sidebar admin open onNav={() => {}} onToggle={() => {}} />)
  expect(html).toContain('>管理</div>')
  for (const label of labels) expect(html).toContain(`title="${label}"`)
  expect(NAV.find(group => group.group === '管理').adminOnly).toBe(true)
})

it('普通账号和身份未提供时均不展示管理入口', () => {
  for (const admin of [false, undefined]) {
    const html = renderToStaticMarkup(<Sidebar admin={admin} open onNav={() => {}} onToggle={() => {}} />)
    expect(html).toContain('title="设置"')
    expect(html).not.toContain('>管理</div>')
    for (const label of labels) expect(html).not.toContain(`title="${label}"`)
  }
})

it('普通账号直接打开管理页面不能挂载管理表单', () => {
  for (const page of NAV.find(group => group.adminOnly).items) {
    const html = renderToStaticMarkup(<AdminPage admin={false} page={page.key} title={page.label} />)
    expect(html).toContain('此页面仅管理员可访问')
    expect(html).not.toContain('<form')
  }
})

it('模型管理页面不混入个人 AI 偏好', () => {
  const html = renderToStaticMarkup(<ModelSettings admin managementOnly />)
  expect(html).toContain('模型服务')
  expect(html).not.toContain('我的 AI 设置')
  expect(html).not.toContain('正在读取 AI 设置')
  expect(html).toContain('保存模型')
})

it('通知管理仅展示公共策略，不展示个人渠道表单', () => {
  const html = renderToStaticMarkup(<NotificationSettings admin managementOnly showMessages={false} />)
  expect(html).toContain('保存消息保留策略')
  expect(html).not.toContain('保存渠道')
  expect(html).not.toContain('新增外部渠道')
})
