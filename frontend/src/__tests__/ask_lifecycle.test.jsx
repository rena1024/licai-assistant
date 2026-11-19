import React from 'react'
import { expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { applyAnswerVersion, AnswerVersions, AnswerProgress } from '../components/askShared'

it('备用回答重建独立版本，失败部分保留且不混入新正文', () => {
  const next = applyAnswerVersion({ modelId: 'cloud', typed: '部分回答', err: '中断', answer: null }, { model_id: 'local', version: 2, reason: '重新生成' })
  expect(next.typed).toBe('')
  expect(next.answer).toBe(null)
  expect(next.versions[0].text).toBe('部分回答')
  const html = renderToStaticMarkup(<AnswerVersions turn={next} />)
  expect(html).toContain('部分回答')
  expect(html).toContain('未完成')
  expect(html).toContain('local')
})

it('备用模型的等待状态显示在回答区域，有工具步骤时也能看到', () => {
  const html = renderToStaticMarkup(<AnswerProgress active turn={{ steps: [{ tool: 'synthetic' }], done: false }} label="正在调用 deepseek-flash（云端） · 已等待 65 秒" />)
  expect(html).toContain('role="status"')
  expect(html).toContain('deepseek-flash')
  expect(html).toContain('65 秒')
})

it('回答完成或失败后不再显示正在调用', () => {
  for (const turn of [{ done: true }, { err: '上下文超限' }, { answer: '完成' }]) {
    expect(renderToStaticMarkup(<AnswerProgress active turn={turn} label="正在调用" />)).toBe('')
  }
})
