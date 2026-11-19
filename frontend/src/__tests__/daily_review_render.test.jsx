import React, { useState } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, expect, it, vi } from 'vitest'
import DailyReview from '../components/DailyReview'

vi.mock('react', async importOriginal => {
  const original = await importOriginal()
  return { ...original, useState: vi.fn(original.useState) }
})
vi.mock('../hooks/useLLMStatus', () => ({
  default: () => ({ headers: {}, label: '正在准备 AI 请求…' }),
}))
afterEach(() => vi.clearAllMocks())

it('复盘可展示包含标题和说明的全球大事与明日关注', () => {
  vi.mocked(useState).mockImplementationOnce(() => [{
    summary: '合成复盘',
    global: [{ title: '合成市场事件', note: '合成影响说明' }, '普通文本事件'],
    tomorrow: [{ title: '合成关注事项', note: '合成关注说明' }],
  }, vi.fn()])
  const html = renderToStaticMarkup(<DailyReview />)
  for (const text of ['合成市场事件', '合成影响说明', '普通文本事件', '合成关注事项', '合成关注说明']) {
    expect(html).toContain(text)
  }
  expect(html).not.toContain('[object Object]')
})

it('复盘兼容空列表及无效的列表条目', () => {
  vi.mocked(useState).mockImplementationOnce(() => [{
    summary: '合成复盘',
    global: [null, {}, { title: '保留有效事件', note: { unexpected: true } }],
    tomorrow: null,
  }, vi.fn()])
  const html = renderToStaticMarkup(<DailyReview />)
  expect(html).toContain('保留有效事件')
  expect(html).not.toContain('[object Object]')
})
