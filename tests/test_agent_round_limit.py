"""取数轮数耗尽后仍利用现有结果收尾，不把超限提示当成答案。"""
import asyncio
import copy

import pytest

from services import stock_agent as agent


@pytest.mark.parametrize('stream', [True, False])
@pytest.mark.parametrize('ending', ['answer', 'error', 'truncated', 'tool', 'guard'])
def test_round_limit_gets_one_tool_free_summary(monkeypatch, stream, ending):
    calls, executed = [], []
    tools = [{'name': 'calc', 'description': '计算', 'input_schema': {'type': 'object'}}]
    def generate(messages, system, model, max_tokens, available_tools, **kwargs):
        calls.append((copy.deepcopy(messages), copy.deepcopy(available_tools)))
        if len(calls) <= 2 or ending == 'tool':
            return {'content': [{'type': 'tool_use', 'id': str(len(calls)), 'name': 'calc',
                                 'input': {}}], 'stop_reason': 'tool_use'}
        if ending == 'error':
            raise RuntimeError('总结失败')
        return {'content': [{'type': 'text', 'text': '依据已有数据形成的结论'}],
                'stop_reason': 'max_tokens' if ending == 'truncated' else 'end_turn'}
    async def active_tools():
        return tools
    async def run_tool(call):
        executed.append(call['id'])
        return {'total': 42}
    monkeypatch.setattr(agent, '_MAX_ROUNDS', 2)
    monkeypatch.setattr(agent._llm, 'call_claude_messages', generate)
    monkeypatch.setattr(agent, '_system', lambda: '分析规则')
    monkeypatch.setattr(agent, '_active_tools', active_tools)
    monkeypatch.setattr(agent, '_run_tool', run_tool)
    monkeypatch.setattr(agent._guard, 'inspect', lambda *args: ['需修正'] if ending == 'guard' else [])
    async def run():
        if stream:
            return [e async for e in agent.ask_stock_stream('分析持仓')]
        return await agent.ask_stock('分析持仓')
    result = asyncio.run(run())
    assert len(calls) == 3
    assert calls[-1][1] == []
    assert executed == ['1', '2']
    # 总结收到此前两轮工具结果，没有重新取数。
    tool_results = [b for m in calls[-1][0] if isinstance(m['content'], list)
                    for b in m['content'] if b.get('type') == 'tool_result']
    assert len(tool_results) == 2
    assert all('42' in b['content'] for b in tool_results)
    if stream:
        assert any(e['type'] == 'answer' for e in result) == (ending == 'answer')
        assert any(e['type'] == 'error' for e in result) == (ending != 'answer')
        if ending == 'truncated':
            assert any(e['type'] == 'partial' and e['text'] for e in result)
    else:
        assert bool(result.get('answer')) == (ending == 'answer')
        assert bool(result.get('error')) == (ending != 'answer')
        if ending == 'truncated':
            assert result.get('partial')
