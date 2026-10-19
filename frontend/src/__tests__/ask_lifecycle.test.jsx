import React from 'react'
import { expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { applyAnswerVersion, AnswerVersions } from '../components/askShared'

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
