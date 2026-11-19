"""每日页面分析只自动尝试一次，跨页面和进程生命周期复用持久结果。"""
import asyncio

import pytest

from services import ai_jobs
from services.user_context import user_scope
from tests.test_ownership_portfolio import owners


def test_same_day_persists_across_event_loops_and_manual_refresh_updates_result(owners):
    owner, _, _ = owners
    calls = []
    @ai_jobs.shared_ai_job(daily=True)
    async def summary(force=False):
        calls.append(force)
        return {'summary': f'结果{len(calls)}'}
    async def visit(force=False):
        with user_scope(owner):
            return await summary(force=force)
    assert asyncio.run(visit()) == {'summary': '结果1'}
    assert asyncio.run(visit()) == {'summary': '结果1'}
    assert asyncio.run(visit(True)) == {'summary': '结果2'}
    assert asyncio.run(visit()) == {'summary': '结果2'}
    assert calls == [True, True]


def test_day_parameters_and_owner_are_independent(owners, monkeypatch):
    from services import daily_ai_cache
    first, second, _ = owners
    current_day = ['2026-11-19']
    monkeypatch.setattr(daily_ai_cache, 'today', lambda: current_day[0])
    calls = []
    @ai_jobs.shared_ai_job(daily=True)
    async def summary(days=10, force=False):
        calls.append(days)
        return {'summary': str(len(calls))}
    async def visit(owner, days=10):
        with user_scope(owner):
            return await summary(days=days)
    assert asyncio.run(visit(first))['summary'] == '1'
    assert asyncio.run(visit(first))['summary'] == '1'
    assert asyncio.run(visit(second))['summary'] == '2'
    assert asyncio.run(visit(first, 20))['summary'] == '3'
    current_day[0] = '2026-11-20'
    assert asyncio.run(visit(first))['summary'] == '4'


@pytest.mark.parametrize('failure', ['exception', 'body'])
def test_failure_does_not_automatically_retry_but_manual_refresh_can(owners, failure):
    owner, _, _ = owners
    calls = []
    @ai_jobs.shared_ai_job(daily=True)
    async def summary(force=False):
        calls.append(force)
        if len(calls) == 1:
            if failure == 'exception':
                raise RuntimeError('合成失败')
            return {'error': '合成失败'}
        return {'summary': '手动完成'}
    async def visit(force=False):
        with user_scope(owner):
            return await summary(force=force)
    if failure == 'exception':
        with pytest.raises(RuntimeError, match='合成失败'):
            asyncio.run(visit())
    else:
        assert asyncio.run(visit()).get('error')
    assert asyncio.run(visit()).get('error')
    assert len(calls) == 1
    assert asyncio.run(visit(True)) == {'summary': '手动完成'}
    assert asyncio.run(visit()) == {'summary': '手动完成'}
    assert len(calls) == 2


def test_manual_failure_keeps_previous_successful_result(owners):
    owner, _, _ = owners
    calls = []
    @ai_jobs.shared_ai_job(daily=True)
    async def summary(force=False):
        calls.append(force)
        return {'summary': '已有结果'} if len(calls) == 1 else {'error': '合成失败'}
    async def visit(force=False):
        with user_scope(owner):
            return await summary(force=force)
    assert asyncio.run(visit()) == {'summary': '已有结果'}
    assert asyncio.run(visit(True)).get('error')
    assert asyncio.run(visit()) == {'summary': '已有结果'}


def test_daily_concurrent_viewers_share_one_generation(owners):
    owner, _, _ = owners
    calls = []
    @ai_jobs.shared_ai_job(daily=True)
    async def summary(force=False):
        calls.append(force)
        await asyncio.sleep(0)
        return {'summary': '完成'}
    async def visit():
        with user_scope(owner):
            return await asyncio.gather(summary(), summary(force=True), summary())
    assert len(asyncio.run(visit())) == 3
    assert calls == [True]


def test_interrupted_attempt_is_not_automatically_restarted(owners):
    owner, _, _ = owners
    calls = []
    @ai_jobs.shared_ai_job(daily=True)
    async def summary(force=False):
        calls.append(force)
        raise asyncio.CancelledError()
    async def visit():
        with user_scope(owner):
            return await summary()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(visit())
    assert asyncio.run(visit()).get('error')
    assert calls == [True]


def test_previous_day_late_result_does_not_overwrite_new_day(owners, monkeypatch):
    from services import daily_ai_cache
    owner, _, _ = owners
    day = ['2026-11-19']
    monkeypatch.setattr(daily_ai_cache, 'today', lambda: day[0])
    async def exercise():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        @ai_jobs.shared_ai_job(daily=True)
        async def summary(force=False):
            calls.append(day[0])
            if len(calls) == 1:
                entered.set()
                await release.wait()
                return {'summary': '昨天结果'}
            return {'summary': '今天结果'}
        with user_scope(owner):
            yesterday = asyncio.create_task(summary())
            await entered.wait()
            day[0] = '2026-11-20'
            assert await summary() == {'summary': '今天结果'}
            release.set()
            await yesterday
            assert await summary() == {'summary': '今天结果'}
            assert len(calls) == 2
    asyncio.run(exercise())


def test_all_daily_routes_are_wrapped_in_shared_jobs():
    from api import market_routes, news_routes, portfolio_routes, sector_routes
    for function in (market_routes.market_structure, market_routes.sentiment_ai,
                     news_routes.news_digest, news_routes.daily_review,
                     portfolio_routes.trade_review_ai, sector_routes.sector_trend_ai):
        assert hasattr(function, '__wrapped__'), function.__name__
