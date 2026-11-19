import { afterEach, expect, it, vi } from 'vitest'
import { startRun, startDeepDive, followRun } from '../components/askShared'

afterEach(() => vi.unstubAllGlobals())

it.each([startRun, startDeepDive])('分析启动失败时展示后端配置提示', async start => {
  const message = '问答模型未开放工具调用能力，请到设置调整。'
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(
    JSON.stringify({ detail: { code: 'controlled_ai_not_ready', message } }),
    { status: 503, headers: { 'Content-Type': 'application/json' } },
  )))
  await expect(start({ question: 'synthetic' })).rejects.toThrow(message)
})

it('分析启动失败时保留字符串错误说明', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(
    JSON.stringify({ detail: '系统维护中，请稍后重试' }), { status: 503 },
  )))
  await expect(startRun({ question: 'synthetic' })).rejects.toThrow('系统维护中')
})

it('分析启动成功时返回服务端任务信息', async () => {
  const run = { run_id: 'synthetic', session_id: 1, cursor: 0 }
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(run))))
  await expect(startRun({ question: 'synthetic' })).resolves.toEqual(run)
})

it('跟看接口失败时显示错误，不继续空转', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(
    JSON.stringify({ detail: { message: '系统维护中' } }), { status: 503 },
  )))
  const error = await new Promise(resolve => followRun('synthetic', { onError: resolve }))
  expect(error).toContain('系统维护中')
})

it('事件流未收到结束标记就关闭时，明确提示连接中断', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(
    'data: {"type":"version","model_id":"deepseek_flash","version":2}\n\n',
  )))
  const result = await new Promise(resolve => followRun('synthetic', {
    onEnd: () => resolve('unexpected end'), onError: resolve,
  }))
  expect(result).toContain('进度连接已中断')
})
