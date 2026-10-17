import React from 'react'
import { expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { BackupList } from '../components/OperationsPanel'

it('恢复点只展示元信息，恢复须单独确认且忙碌时禁用', () => {
  const html = renderToStaticMarkup(<BackupList points={[
    { id: 'manual-123-abc', kind: 'manual', created_at: 123, file_count: 3 },
  ]} busy onSelect={() => {}} />)
  expect(html).toContain('manual-123-abc')
  expect(html).toContain('选择恢复点')
  expect(html).toContain('disabled')
  expect(html).not.toContain('下载')
})
