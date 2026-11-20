import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError
from tests.test_ownership_portfolio import owners
from services.model_catalog import ModelConfig
from services.llm_types import CallContext, ModelRequest, Usage, LLMError
from services.user_context import user_scope


def model(**updates):
    values = dict(id='timed', protocol='deepseek', endpoint='https://api.deepseek.com',
                  model='synthetic', api_key='synthetic', price=dict(currency='CNY', exchange_rate='1',
                  input='10', output='20', cache_read='2', cache_write='0'),
                  time_pricing={'off_peak': {'input': '5', 'output': '8', 'cache_read': '1'}})
    values.update(updates)
    return ModelConfig(**values)


def at(value):
    return datetime.fromisoformat(value)


@pytest.mark.parametrize('moment,tariff', [
    ('2026-11-19T08:59:59+08:00', 'off_peak'), ('2026-11-19T09:00:00+08:00', 'peak'),
    ('2026-11-19T11:59:59+08:00', 'peak'), ('2026-11-19T12:00:00+08:00', 'off_peak'),
    ('2026-11-19T13:59:59+08:00', 'off_peak'), ('2026-11-19T14:00:00+08:00', 'peak'),
    ('2026-11-19T17:59:59+08:00', 'peak'), ('2026-11-19T18:00:00+08:00', 'off_peak'),
    ('2026-11-19T01:00:00+00:00', 'peak'), ('2026-10-01T10:00:00+08:00', 'off_peak'),
    ('2026-10-10T10:00:00+08:00', 'off_peak'), ('2026-11-22T10:00:00+08:00', 'off_peak'),
])
def test_boundaries_weekends_makeup_days_and_holidays(moment, tariff):
    from services.model_pricing import snapshot
    value = snapshot(model(), now=at(moment))
    assert value['tariff'] == tariff
    assert Decimal(value['input']) == (10 if tariff == 'peak' else 5)
    assert value['priced_at'] == int(at(moment).timestamp())
    assert value['pricing_basis'] == 'request_start'
    assert value['calendar_version']


def test_unknown_calendar_year_fails_closed_and_fixed_price_is_compatible():
    from services.model_pricing import snapshot
    with pytest.raises(LLMError) as error:
        snapshot(model(), now=at('2200-01-02T10:00:00+08:00'))
    assert error.value.code == 'pricing_calendar_unavailable'
    assert snapshot(model(time_pricing=None), now=at('2200-01-02T10:00:00+08:00'))['input'] == '10'
    with pytest.raises(ValueError):
        snapshot(model(), now=datetime(2026, 11, 19, 10))


@pytest.mark.parametrize('updates', [
    {'protocol': 'openai'}, {'time_pricing': {'off_peak': {'input': '-1', 'output': '2', 'cache_read': '0'}}},
    {'time_pricing': {'off_peak': {'input': '1', 'output': 'NaN', 'cache_read': '0'}}},
    {'time_pricing': {'off_peak': {'input': '1', 'output': '2'}}}, {'price': None, 'enabled': False},
    {'price': {'currency': 'USD', 'exchange_rate': '7', 'input': '10', 'output': '20', 'cache_read': '2', 'cache_write': '0'}},
])
def test_invalid_time_prices_are_rejected(updates):
    with pytest.raises(ValidationError):
        model(**updates)


def test_reservation_uses_componentwise_maximum_even_when_off_peak_costs_more():
    from services import llm_runtime
    from services.model_pricing import reservation_price
    m = model(time_pricing={'off_peak': {'input': '15', 'output': '8', 'cache_read': '3'}})
    ceiling = reservation_price(m)
    assert (ceiling.input, ceiling.output, ceiling.cache_read) == (15, 20, 3)
    request = ModelRequest(messages=[{'role': 'user', 'content': 'synthetic'}])
    assert llm_runtime._reservation(m, request) == llm_runtime._reservation(m.model_copy(update={'price': ceiling, 'time_pricing': None}), request)


def test_cross_period_settlement_retry_and_configuration_changes_keep_snapshots(owners):
    from services import budget
    from services.model_pricing import snapshot
    from database import get_db
    a, b, _ = owners
    async def exercise():
        with user_scope(a):
            await budget.set_limits(0, day='10', month='10')
            await budget.set_limits(b, day='10', month='10')
        with user_scope(b):
            ctx = CallContext(b, 'ask', 'timed-task')
            start = at('2026-11-19T11:59:59+08:00')
            first = snapshot(model(), now=start)
            rid = await budget.reserve_attempt(ctx, 'timed', first, 10000, 'first', now=start)
            # 配置已变、时段已过：原尝试仍读自己的快照，重复结算不得重算。
            changed = model(time_pricing={'off_peak': {'input': '1', 'output': '1', 'cache_read': '0'}})
            second = snapshot(changed, now=at('2026-11-19T12:00:00+08:00'))
            retry = await budget.reserve_attempt(ctx, 'timed', second, 10000, 'retry', now=at('2026-11-19T12:00:00+08:00'))
            usage = Usage(input_tokens=100, output_tokens=100, cache_read_tokens=20)
            await budget.settle_attempt(rid, usage)
            await budget.settle_attempt(rid, Usage(input_tokens=1, output_tokens=1))
            await budget.settle_attempt(retry, usage)
            db = await get_db()
            try:
                rows = {r['id']: dict(r) for r in await (await db.execute('SELECT * FROM llm_attempts')).fetchall()}
                assert rows[rid]['cost'] == 2840
                assert rows[retry]['cost'] == 180
                assert json.loads(rows[rid]['price'])['tariff'] == 'peak'
                assert json.loads(rows[retry]['price'])['tariff'] == 'off_peak'
                assert rows[rid]['day'] == '2026-11-19'
            finally:
                await db.close()
    asyncio.run(exercise())


def test_physical_retry_selects_new_tariff_and_preserves_first_attempt(owners, monkeypatch):
    from types import SimpleNamespace
    from services import model_pricing, llm_runtime, budget, model_catalog, runtime_state
    from database import get_db
    a, b, _ = owners
    clock = [at('2026-11-19T11:59:59+08:00')]
    monkeypatch.setattr(model_pricing, 'datetime', SimpleNamespace(now=lambda tz: clock[0]))
    calls = []
    def send(*args):
        calls.append(True)
        if len(calls) == 1:
            clock[0] = at('2026-11-19T12:00:00+08:00')
            raise llm_runtime.TransportError('provider_http_error', known_unbilled=True)
        return {'choices': [{'message': {'content': 'synthetic answer'}, 'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 100, 'completion_tokens': 100, 'prompt_cache_hit_tokens': 20, 'prompt_cache_miss_tokens': 80}}
    monkeypatch.setattr(llm_runtime, 'send_http', send)
    async def exercise():
        with user_scope(a):
            data = model().model_dump(mode='json'); data['api_key'] = 'synthetic'
            await model_catalog.save_model(data, expected_version=0)
            await budget.set_limits(0, day='10', month='10')
            await budget.set_limits(b, day='10', month='10')
            runtime_state.update_state(cloud_hold=False)
        with user_scope(b):
            await model_catalog.save_preferences(local_only=False, scenes={'ask': ['timed']})
            await llm_runtime.generate(CallContext(b, 'ask', 'physical-retry'), ModelRequest(messages=[]))
            db = await get_db()
            try:
                rows = [dict(r) for r in await (await db.execute('SELECT * FROM llm_attempts')).fetchall()]
                tariffs = {json.loads(row['price'])['tariff']: row for row in rows}
                assert len(rows) == 2 and len(calls) == 2
                assert tariffs['peak']['cost'] == 0
                assert tariffs['off_peak']['cost'] == 1220
                assert tariffs['peak']['reserved'] == tariffs['off_peak']['reserved']
            finally:
                await db.close()
    asyncio.run(exercise())
