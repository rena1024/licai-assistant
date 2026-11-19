import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock

import pytest

from tests.test_ownership_portfolio import owners
from services.user_context import user_scope

CST = timezone(timedelta(hours=8))


@pytest.mark.parametrize('moment,expected', [
    (datetime(2026, 10, 28, 8, 59, tzinfo=CST), 0),
    (datetime(2026, 10, 28, 9, 0, 30, tzinfo=CST), 1),
    (datetime(2026, 10, 28, 9, 1, tzinfo=CST), 0),
    (datetime(2026, 10, 31, 9, 0, tzinfo=CST), 1),
    (datetime(2026, 11, 1, 9, 0, tzinfo=CST), 1),
    (datetime(2026, 10, 28, 1, 0, tzinfo=timezone.utc), 1),
])
def test_schedule_uses_beijing_nine_including_weekends(owners, monkeypatch, moment, expected):
    from database import set_user_setting
    from services import scheduler, morning_briefing
    _, b, _ = owners
    generate = AsyncMock(return_value=[{'stock_code': '600000', 'summary': '合成简报'}])
    monkeypatch.setattr(morning_briefing, 'generate_all_briefings', generate)

    async def exercise():
        await set_user_setting('automatic_ai_enabled', '1', user_id=b)
        with user_scope(b):
            await scheduler.briefing_tick(now=moment)
    asyncio.run(exercise())
    assert generate.await_count == expected
    if expected:
        generate.assert_awaited_once_with(notify=False)


@pytest.mark.parametrize('external_enabled', [False, True])
def test_enabled_user_runs_once_per_day_and_respects_external_channel(owners, monkeypatch, external_enabled):
    import sqlite3
    from database import set_user_setting
    from services import scheduler, morning_briefing, notifications as n, notification_delivery as d
    a, b, path = owners
    generate = AsyncMock(return_value=[{'stock_code': '600000', 'summary': '合成简报'}])
    monkeypatch.setattr(morning_briefing, 'generate_all_briefings', generate)
    moment = datetime(2026, 10, 28, 9, 0, tzinfo=CST)

    async def exercise():
        with user_scope(a):
            await scheduler.briefing_tick(now=moment)
            assert await n.list_messages() == []
        await set_user_setting('automatic_ai_enabled', '1', user_id=b)
        with user_scope(b):
            channel = await d.save_channel({'kind': 'pushplus', 'name': '合成渠道', 'config': {'token': 'fake'}}, expected_version=0)
            await d.activate(channel['id'] if external_enabled else None)
            await scheduler.briefing_tick(now=moment)
        # 新协程不复用内存状态，仍由持久执行记录阻止重跑。
        with user_scope(b):
            await scheduler.briefing_tick(now=moment)
            messages = await n.list_messages()
            assert len(messages) == 1
            assert messages[0]['title'] == '早盘简报已生成'
            assert messages[0]['delivery_state'] == ('pending' if external_enabled else None)
    asyncio.run(exercise())
    assert generate.await_count == 1
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT COUNT(*) FROM notification_deliveries').fetchone()[0] == int(external_enabled)


@pytest.mark.parametrize('results,title', [
    ([{'error': '合成模型错误'}], '早盘简报生成失败'),
    ([{'summary': '可用'}, {'error': '合成错误'}], '早盘简报已更新（部分失败）'),
    ([], '早盘简报未生成'),
    (RuntimeError('synthetic-private-detail'), '早盘简报生成失败'),
])
def test_failure_and_empty_results_are_reported_without_retry(owners, monkeypatch, results, title):
    from database import set_user_setting
    from services import scheduler, morning_briefing, notifications as n
    _, b, _ = owners
    generate = AsyncMock(side_effect=results) if isinstance(results, Exception) else AsyncMock(return_value=results)
    monkeypatch.setattr(morning_briefing, 'generate_all_briefings', generate)

    async def exercise():
        await set_user_setting('automatic_ai_enabled', '1', user_id=b)
        with user_scope(b):
            for _ in range(2):
                await scheduler.briefing_tick(now=datetime(2026, 10, 28, 9, 0, tzinfo=CST))
            messages = await n.list_messages()
            assert len(messages) == 1
            assert messages[0]['title'] == title
            assert 'synthetic-private-detail' not in messages[0]['body']
    asyncio.run(exercise())
    assert generate.await_count == 1


def test_scheduled_generator_can_save_without_duplicate_notification(owners, monkeypatch):
    import database
    from services import morning_briefing, market_data, notifications as n
    _, b, _ = owners
    monkeypatch.setattr(database, 'get_all_holdings', AsyncMock(return_value=[{
        'stock_code': '600000', 'stock_name': '合成持仓', 'shares': 100, 'cost_price': 10,
    }]))
    monkeypatch.setattr(database, 'list_external_assets', AsyncMock(return_value=[]))
    monkeypatch.setattr(market_data, 'get_realtime_quotes', AsyncMock(return_value={'600000': {'price': 11}}))
    monkeypatch.setattr(morning_briefing, 'generate_briefing_for_stock', AsyncMock(return_value={'stock_code': '600000', 'summary': '合成简报'}))

    async def exercise():
        with user_scope(b):
            results = await morning_briefing.generate_all_briefings(notify=False)
            assert len(results) == 1
            assert await database.get_briefings_for_date(results[0]['briefing_date'], user_id=b)
            assert await n.list_messages() == []
    asyncio.run(exercise())


def test_per_stock_exception_is_not_reported_as_full_success(owners, monkeypatch):
    import database
    from services import scheduler, morning_briefing, market_data, notifications as n
    _, b, _ = owners
    monkeypatch.setattr(database, 'get_all_holdings', AsyncMock(return_value=[
        {'stock_code': code, 'stock_name': '合成持仓', 'shares': 100, 'cost_price': 10}
        for code in ('600000', '600001')
    ]))
    monkeypatch.setattr(database, 'list_external_assets', AsyncMock(return_value=[]))
    monkeypatch.setattr(market_data, 'get_realtime_quotes', AsyncMock(return_value={code: {'price': 11} for code in ('600000', '600001')}))
    monkeypatch.setattr(morning_briefing, 'generate_briefing_for_stock', AsyncMock(side_effect=[
        {'stock_code': '600000', 'summary': '合成简报'}, RuntimeError('synthetic-private-detail'),
    ]))

    async def exercise():
        await database.set_user_setting('automatic_ai_enabled', '1', user_id=b)
        with user_scope(b):
            await scheduler.briefing_tick(now=datetime(2026, 10, 28, 9, 0, tzinfo=CST))
            messages = await n.list_messages()
            assert len(messages) == 1
            assert messages[0]['title'] == '早盘简报已更新（部分失败）'
            assert 'synthetic-private-detail' not in messages[0]['body']
    asyncio.run(exercise())


def test_dispatch_keeps_scene_background_and_next_day_settings(owners, monkeypatch):
    from database import set_user_setting
    from services import scheduler, morning_briefing, llm_runtime
    from services.user_context import require_user_id
    _, b, _ = owners
    calls = []

    async def generate(*, notify=True):
        calls.append((require_user_id(), llm_runtime._scene.get(), llm_runtime._background.get(), notify))
        return [{'summary': '合成简报'}]
    monkeypatch.setattr(morning_briefing, 'generate_all_briefings', generate)

    async def exercise():
        await set_user_setting('automatic_ai_enabled', '1', user_id=b)
        for day in (28, 28, 29):
            moment = datetime(2026, 10, day, 9, 0, tzinfo=CST)
            await scheduler.dispatch_personal(lambda: scheduler.briefing_tick(now=moment), ai=True)
        await set_user_setting('automatic_ai_enabled', '0', user_id=b)
        await scheduler.dispatch_personal(lambda: scheduler.briefing_tick(now=datetime(2026, 10, 30, 9, 0, tzinfo=CST)), ai=True)
    asyncio.run(exercise())
    assert calls == [(b, 'briefing', True, False)] * 2
