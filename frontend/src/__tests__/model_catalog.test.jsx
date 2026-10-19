import React from 'react'
import { expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { ModelChoices } from '../components/ModelSettings'

it('保留已失效选择并展示主备顺序，无候选可保存仅本地', () => {
  const html = renderToStaticMarkup(<ModelChoices scene="ask" ids={['old']} models={[]} onChange={() => {}} />)
  expect(html).toContain('已失效')
  expect(html).toContain('备用 1')
  expect(html).toContain('备用 2')
  expect(html).toContain('value="old"')
  expect(renderToStaticMarkup(<ModelChoices scene="ask" ids={[]} models={[]} onChange={() => {}} />)).toContain('AI 暂不可用')
})
