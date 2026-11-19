import asyncio
import threading

import pytest

from tests.test_ownership_portfolio import owners
from services.user_context import user_scope


def test_cancelled_view_does_not_cancel_job_and_reopening_joins_it(owners):
    from services.ai_jobs import shared_ai_job
    owner, _, _ = owners

    async def exercise():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        @shared_ai_job
        async def summary(days=10, force=False):
            calls.append(days)
            entered.set()
            await release.wait()
            return {'summary': '完整结果'}

        with user_scope(owner):
            first = asyncio.create_task(summary())
            await entered.wait()
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            second = asyncio.create_task(summary(days=10, force=True))
            await asyncio.sleep(0)
            assert calls == [10]
            release.set()
            assert await second == {'summary': '完整结果'}

    asyncio.run(exercise())


def test_jobs_isolate_owners_parameters_and_clean_up_failures(owners):
    from services.ai_jobs import shared_ai_job
    a, b, _ = owners

    async def exercise():
        release = asyncio.Event()
        calls = []

        @shared_ai_job
        async def summary(days=10):
            from services.user_context import require_user_id
            calls.append((require_user_id(), days))
            await release.wait()
            raise ValueError('合成错误')

        tasks = []
        for owner, days in [(a, 10), (a, 20), (b, 10)]:
            with user_scope(owner):
                tasks.append(asyncio.create_task(summary(days)))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert sorted(calls) == [(a, 10), (a, 20), (b, 10)]
        release.set()
        assert all(isinstance(r, ValueError) for r in await asyncio.gather(*tasks, return_exceptions=True))
        with user_scope(a), pytest.raises(ValueError):
            await summary()
        assert calls.count((a, 10)) == 2

    asyncio.run(exercise())


def test_market_summary_survives_caller_cancel_and_caches_finished_result(owners, monkeypatch):
    from api import market_routes
    from services import llm_client
    owner, _, _ = owners
    entered, release = threading.Event(), threading.Event()
    calls = []

    async def sentiment():
        return {'date': '2099-01-01', 'n_zt': 1, 'ladder': [], 'hot_sectors': []}

    def generate(*args, **kwargs):
        calls.append(1)
        entered.set()
        assert release.wait(5)
        return '{"summary":"完整结果","points":[]}'

    monkeypatch.setattr(market_routes, 'market_sentiment', sentiment)
    monkeypatch.setattr(market_routes, '_senti_ai_cache', {})
    monkeypatch.setattr(llm_client, 'call_claude', generate)

    async def exercise():
        with user_scope(owner):
            first = asyncio.create_task(market_routes.sentiment_ai())
            try:
                assert await asyncio.to_thread(entered.wait, 5)
                first.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await first
                second = asyncio.create_task(market_routes.sentiment_ai())
                release.set()
                assert (await second)['summary'] == '完整结果'
                assert (await market_routes.sentiment_ai())['summary'] == '完整结果'
                assert calls == [1]
            finally:
                release.set()

    asyncio.run(exercise())


def test_reopened_request_can_read_actual_job_activity_without_other_owner_access(owners):
    from services.ai_jobs import shared_ai_job
    from services import llm_activity, llm_runtime
    from services.llm_types import CallContext
    from services.model_catalog import ModelConfig
    a, b, _ = owners

    async def exercise():
        entered, release = asyncio.Event(), asyncio.Event()
        model = ModelConfig(id='local', protocol='ollama', endpoint='http://localhost:11434',
                            model='synthetic', local=True)

        @shared_ai_job
        async def summary():
            with llm_activity.track('joined-activity', CallContext(a, 'market', llm_runtime._task.get()), model):
                entered.set()
                await release.wait()
                return {'summary': '合成结果'}

        with user_scope(a), llm_activity.request_scope('a' * 32):
            first = asyncio.create_task(summary())
            await entered.wait()
        with user_scope(a), llm_activity.request_scope('b' * 32):
            second = asyncio.create_task(summary())
            await asyncio.sleep(0)
            assert llm_activity.snapshot(request_id='b' * 32)[0]['model_id'] == 'local'
        with user_scope(b):
            assert llm_activity.snapshot(request_id='b' * 32) == []
        release.set()
        await asyncio.gather(first, second)
        with user_scope(a):
            assert llm_activity.snapshot(request_id='b' * 32) == []

    asyncio.run(exercise())


def test_cloud_can_run_while_local_model_is_busy_and_second_local_waits(owners, monkeypatch):
    from services import llm_runtime, model_catalog, local_queue
    from services.llm_types import CallContext, ModelRequest, ModelResult, Usage
    from tests.test_llm_attempts import configure
    a, b, _ = owners
    entered, release = threading.Event(), threading.Event()
    order = []

    async def execute(context, request, identifier, attempt):
        order.append(context.task_id)
        if context.task_id == 'local-first':
            entered.set()
            assert release.wait(5)
        return ModelResult([{'type': 'text', 'text': 'OK'}], Usage(input_tokens=1, output_tokens=1))

    monkeypatch.setattr(llm_runtime, '_execute', execute)

    async def exercise():
        await configure(a, b)
        with user_scope(a):
            await model_catalog.save_model(dict(id='local-job', protocol='ollama',
                endpoint='http://localhost:11434', model='synthetic', local=True), expected_version=0)
        with user_scope(b):
            await model_catalog.save_preferences(local_only=False, scenes={
                'market': ['local-job'], 'sector': ['cloud']})
            first = asyncio.create_task(llm_runtime.generate(CallContext(b, 'market', 'local-first'), ModelRequest(messages=[])))
            second = None
            try:
                assert await asyncio.to_thread(entered.wait, 5)
                second = asyncio.create_task(llm_runtime.generate(CallContext(b, 'market', 'local-second'), ModelRequest(messages=[])))
                assert await asyncio.to_thread(local_queue.queue.wait_for_size, 1, 5)
                cloud = await asyncio.wait_for(llm_runtime.generate(CallContext(b, 'sector', 'cloud-parallel'), ModelRequest(messages=[])), timeout=2)
                assert cloud.content[0]['text'] == 'OK'
                assert order == ['local-first', 'cloud-parallel']
            finally:
                release.set()
                await asyncio.gather(first, *([second] if second else []))
            assert order == ['local-first', 'cloud-parallel', 'local-second']

    asyncio.run(exercise())
