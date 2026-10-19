import asyncio
from datetime import datetime, timezone
import pytest
from tests.test_ownership_portfolio import owners
from services.user_context import user_scope
from services.llm_types import CallContext, Usage, LLMError

PRICE = {'currency': 'CNY', 'exchange_rate': '1', 'input': '10', 'output': '20',
         'cache_read': '1', 'cache_write': '12'}


def test_atomic_reservations_never_exceed_limit(owners):
    from services import budget
    a, b, _ = owners
    async def exercise():
        with user_scope(a):
            await budget.set_limits(0, day='0.000100', month='1')
            await budget.set_limits(b, day='0.000100', month='1')
        with user_scope(b):
            ctx = CallContext(b, 'ask', 'task')
            result = await asyncio.gather(*[budget.reserve_attempt(ctx, 'cloud', PRICE, 30, f'key{i}') for i in range(10)], return_exceptions=True)
            ids = [i for i in result if isinstance(i, str)]
            assert len(ids) == 3
            assert len([e for e in result if isinstance(e, LLMError)]) == 7
            index = next(i for i, value in enumerate(result) if isinstance(value, str))
            assert await budget.reserve_attempt(ctx, 'cloud', PRICE, 30, f'key{index}') == result[index]
            await budget.settle_attempt(ids[0], None)
            with pytest.raises(LLMError, match='待核实'):
                await budget.reserve_attempt(ctx, 'cloud', PRICE, 1, 'blocked')
    asyncio.run(exercise())


def test_original_period_idempotent_settlement_and_missing_budgets(owners):
    from services import budget
    a, b, _ = owners
    async def exercise():
        with user_scope(b):
            with pytest.raises(LLMError):
                await budget.reserve_attempt(CallContext(b, 'ask', 'x'), 'cloud', PRICE, 100, 'missing')
        with user_scope(a):
            await budget.set_limits(0, day='10', month='10')
            await budget.set_limits(b, day='10', month='10')
        at = datetime(2026, 10, 31, 15, 59, tzinfo=timezone.utc)
        with user_scope(b):
            rid = await budget.reserve_attempt(CallContext(b, 'ask', 'x'), 'cloud', PRICE, 20000, 'once', now=at)
            await budget.settle_attempt(rid, Usage(input_tokens=100, output_tokens=100))
            await budget.settle_attempt(rid, Usage(input_tokens=999, output_tokens=999))
            stats = await budget.summary(now=at)
            assert stats['user']['day']['spent'] == 3000
            assert stats['user']['day']['reserved'] == 0
            assert (await budget.summary(now=datetime(2026, 11, 1, tzinfo=timezone.utc)))['user']['month']['spent'] == 0
        with user_scope(a):
            # 诊断仅占全局，不依赖个人额度。
            rid = await budget.reserve_attempt(CallContext(None, 'diagnostic', 'diag', 'diagnostic'), 'cloud', PRICE, 10, 'diag')
            await budget.settle_attempt(rid, Usage(input_tokens=0, output_tokens=0))
    asyncio.run(exercise())


def test_price_cache_accounting_and_validation():
    from services.budget import cost_micro
    assert cost_micro(PRICE, Usage(input_tokens=100, output_tokens=100, cache_read_tokens=30, cache_write_tokens=20, reasoning_tokens=80)) == 2770
    with pytest.raises(ValueError):
        cost_micro(PRICE, Usage(input_tokens=1, output_tokens=0, cache_read_tokens=2))
