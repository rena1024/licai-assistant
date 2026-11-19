"""问答采用 DeepSeek 默认输出额度时，仍应预留费用并校验输入。"""
import asyncio
from types import SimpleNamespace

import pytest

from services import llm_runtime, stock_agent
from services.llm_adapters import deepseek
from services.llm_types import CallContext, LLMError, ModelRequest, ModelResult, Usage
from services.model_catalog import ModelConfig
from tests.test_budget_ledger import PRICE


def model(context_limit=102400):
    return ModelConfig(id='deepseek', protocol='deepseek', endpoint='https://api.deepseek.com',
                       model='deepseek-flash', context_limit=context_limit, api_key='synthetic', price=PRICE)


def test_deepseek_omits_unspecified_output_limit_but_preserves_explicit_limit():
    request = ModelRequest(messages=[{'role': 'user', 'content': '分析持仓'}], output_limit=None)
    payload, _, _ = deepseek.encode(model(), request)
    assert 'max_tokens' not in payload
    payload, _, _ = deepseek.encode(model(), request.model_copy(update={'output_limit': 2048}))
    assert payload['max_tokens'] == 2048


def test_default_output_reserves_thinking_budget_without_treating_it_as_required_space(monkeypatch):
    request = ModelRequest(messages=[{'role': 'user', 'content': '数' * 15000}], output_limit=None)
    usage = []
    monkeypatch.setattr(llm_runtime.budget, 'cost_micro', lambda price, value: usage.append(value) or 100)
    assert llm_runtime._reservation(model(60000), request) == 110
    assert usage[0].output_tokens == 65536
    assert usage[0].input_tokens > 45000


def test_default_output_still_rejects_oversized_input():
    request = ModelRequest(messages=[{'role': 'user', 'content': '数' * 2000}], output_limit=None)
    with pytest.raises(LLMError) as error:
        llm_runtime._reservation(model(4096), request)
    assert error.value.code == 'context_limit'


def test_explicit_output_still_requires_context_space():
    request = ModelRequest(messages=[{'role': 'user', 'content': '数' * 1000}], output_limit=8192)
    with pytest.raises(LLMError) as error:
        llm_runtime._reservation(model(10000), request)
    assert error.value.code == 'context_limit'


def test_ask_requests_provider_default_instead_of_fixed_output_limit(monkeypatch):
    seen = []
    def generate(*args, **kwargs):
        seen.append((args, kwargs))
        return {'content': [{'type': 'text', 'text': '完成'}], 'stop_reason': 'end_turn'}
    async def active_tools():
        return []
    monkeypatch.setattr(stock_agent._llm, 'call_claude_messages', generate)
    monkeypatch.setattr(stock_agent, '_system', lambda: '分析规则')
    monkeypatch.setattr(stock_agent, '_active_tools', active_tools)
    async def run():
        return [event async for event in stock_agent._llm_round([])]
    assert asyncio.run(run())[0][0] == 'resp'
    assert seen[0][0][3] is None


@pytest.mark.parametrize('protocol,expected', [('deepseek', None), ('anthropic', 8192), ('openai', 8192)])
def test_execution_preserves_other_provider_limits_and_settles_usage(monkeypatch, protocol, expected):
    configured = model().model_copy(update={'protocol': protocol})
    request = ModelRequest(messages=[{'role': 'user', 'content': '分析持仓'}], output_limit=None)
    encoded, settled = [], []
    result = ModelResult([{'type': 'text', 'text': '完成'}], Usage(input_tokens=10, output_tokens=20))
    async def authorize(*args):
        return configured, 1
    async def reserve(*args):
        return 'synthetic-attempt'
    async def settle(*args):
        settled.append(args)
    async def metadata(*args):
        pass
    def encode(model, value):
        encoded.append(value.output_limit)
        return {}, '/synthetic', {}
    monkeypatch.setattr(llm_runtime, '_authorize', authorize)
    monkeypatch.setattr(llm_runtime.budget, 'reserve_attempt', reserve)
    monkeypatch.setattr(llm_runtime.budget, 'settle_attempt', settle)
    monkeypatch.setattr(llm_runtime, '_attempt_metadata', metadata)
    monkeypatch.setattr(llm_runtime, 'adapter', lambda _: SimpleNamespace(encode=encode, decode=lambda _: result))
    monkeypatch.setattr(llm_runtime, 'send_http', lambda *args: {})
    monkeypatch.setattr(llm_runtime, 'check_cancelled', lambda: None)
    context = CallContext(owner_id=1, scene='ask', task_id='synthetic-task')
    assert asyncio.run(llm_runtime._execute(context, request, configured.id, 'synthetic')) is result
    assert encoded == [expected]
    assert request.output_limit is None
    assert settled[0][1] == result.usage
