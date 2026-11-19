import asyncio
import threading
import pytest
from fastapi.testclient import TestClient
from services.llm_types import CallContext, ModelRequest
from services.user_context import user_scope
from tests.test_ownership_portfolio import owners
from tests.test_llm_attempts import configure
from tests.test_controlled_ai_api import login


def test_activity_tracks_real_model_is_owner_scoped_and_cleans_up(owners, monkeypatch):
    from services import llm_activity, llm_runtime
    from run import app
    a, b, _ = owners
    entered, release = threading.Event(), threading.Event()
    request_id = 'a' * 32
    def send(*args):
        entered.set()
        assert release.wait(5)
        return {'content': [{'type': 'text', 'text': 'OK'}], 'usage': {'input_tokens': 1, 'output_tokens': 1}}
    monkeypatch.setattr(llm_runtime, 'send_http', send)
    async def exercise():
        await configure(a, b)
        with user_scope(b), llm_activity.request_scope(request_id):
            task = asyncio.create_task(llm_runtime.generate(CallContext(b, 'ask', 'task-one'), ModelRequest(messages=[])))
            try:
                assert await asyncio.to_thread(entered.wait, 5)
                calls = llm_activity.snapshot(request_id=request_id)
                assert len(calls) == 1
                assert calls[0]['model_id'] == 'cloud' and calls[0]['model'] == 'synthetic'
                assert calls[0]['status'] == 'running'
                with TestClient(app) as client:
                    login(client, 'owner-b', 'Synthetic-password-02!')
                    assert client.get('/api/models/activity', params={'request_id': request_id}).json()['calls'] == calls
                    login(client, 'owner-a', 'Synthetic-password-01!')
                    assert client.get('/api/models/activity', params={'request_id': request_id}).json()['calls'] == []
            finally:
                release.set()
                await task
            assert llm_activity.snapshot(request_id=request_id) == []
    asyncio.run(exercise())


def test_activity_correlates_requests_and_updates_queue_phase(owners):
    from services import llm_activity
    from services.model_catalog import ModelConfig
    a, b, _ = owners
    model = ModelConfig(id='local', protocol='ollama', endpoint='http://localhost:11434', model='qwen3:8b', local=True)
    context = CallContext(b, 'news', 'task-two')
    with user_scope(b), llm_activity.request_scope('b' * 32):
        with llm_activity.track('attempt-two', context, model):
            assert llm_activity.snapshot(request_id='c' * 32) == []
            assert llm_activity.snapshot(task_id='task-two')[0]['status'] == 'queued'
            llm_activity.set_running('attempt-two')
            assert llm_activity.snapshot(request_id='b' * 32)[0]['status'] == 'running'
            with user_scope(a):
                assert llm_activity.snapshot(task_id='task-two') == []
        assert llm_activity.snapshot(task_id='task-two') == []


def test_request_header_reaches_worker_and_is_cleared_after_request(owners):
    from fastapi import FastAPI
    from api import auth_routes
    from services import llm_activity
    from services.auth import AuthenticationMiddleware
    from services.runtime_middleware import RuntimeSafetyMiddleware
    from services.model_catalog import ModelConfig
    a, b, _ = owners
    asyncio.run(configure(a, b))
    app = FastAPI()
    app.include_router(auth_routes.router)
    app.add_middleware(AuthenticationMiddleware)
    app.add_middleware(RuntimeSafetyMiddleware)
    request_id = 'd' * 32
    @app.get('/api/probe')
    async def probe():
        def worker():
            model = ModelConfig(id='local', protocol='ollama', endpoint='http://localhost:11434', model='synthetic', local=True)
            with llm_activity.track('header-probe', CallContext(b, 'news', 'header-task'), model):
                return llm_activity.snapshot(request_id=request_id)
        return await asyncio.to_thread(worker)
    with TestClient(app) as client:
        login(client, 'owner-b', 'Synthetic-password-02!')
        assert client.get('/api/probe', headers={'X-LLM-Activity': request_id}).json()[0]['model_id'] == 'local'
        assert client.get('/api/probe').json() == []


def test_activity_switches_to_actual_fallback_and_cleans_up_on_error(owners, monkeypatch):
    from services import llm_activity, llm_runtime, model_catalog
    from services.llm_types import LLMError
    from tests.test_budget_ledger import PRICE
    a, b, _ = owners
    entered, release = threading.Event(), threading.Event()
    request_id = 'e' * 32
    def send(model, *args):
        if model.id == 'cloud':
            assert llm_activity.snapshot(request_id=request_id)[0]['model_id'] == 'cloud'
            raise LLMError('synthetic_failure')
        entered.set()
        assert release.wait(5)
        raise LLMError('synthetic_failure')
    monkeypatch.setattr(llm_runtime, 'send_http', send)
    async def exercise():
        await configure(a, b)
        with user_scope(a):
            await model_catalog.save_model(dict(id='backup', protocol='anthropic', endpoint='https://api.anthropic.com', model='synthetic-backup', api_key='synthetic', price=PRICE), expected_version=0)
        with user_scope(b), llm_activity.request_scope(request_id):
            await model_catalog.save_preferences(local_only=False, scenes={'ask': ['cloud', 'backup']})
            task = asyncio.create_task(llm_runtime.generate(CallContext(b, 'ask', 'fallback-task'), ModelRequest(messages=[])))
            try:
                assert await asyncio.to_thread(entered.wait, 5)
                calls = llm_activity.snapshot(request_id=request_id)
                assert len(calls) == 1 and calls[0]['model'] == 'synthetic-backup'
            finally:
                release.set()
                with pytest.raises(LLMError):
                    await task
            assert llm_activity.snapshot(request_id=request_id) == []
    asyncio.run(exercise())


def test_activity_elapsed_time_uses_actual_phase_and_resets_when_model_runs(owners, monkeypatch):
    from services import llm_activity
    from services.model_catalog import ModelConfig
    _, owner, _ = owners
    now = [100.0]
    monkeypatch.setattr(llm_activity.time, 'monotonic', lambda: now[0])
    model = ModelConfig(id='local', protocol='ollama', endpoint='http://localhost:11434',
                        model='synthetic', local=True)
    with user_scope(owner), llm_activity.track('elapsed-test', CallContext(owner, 'ask', 'elapsed-task'), model):
        now[0] += 65
        call = llm_activity.snapshot(task_id='elapsed-task')[0]
        assert call['elapsed_seconds'] == 65
        assert '_phase_started' not in call
        llm_activity.set_running('elapsed-test')
        now[0] += 3
        assert llm_activity.snapshot(task_id='elapsed-task')[0]['elapsed_seconds'] == 3
