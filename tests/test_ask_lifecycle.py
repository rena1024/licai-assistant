import asyncio
import threading
import pytest
from tests.test_ownership_portfolio import owners
from services.user_context import user_scope
from services.llm_types import CallContext,ModelRequest,LLMError
from tests.test_llm_attempts import configure


@pytest.fixture(autouse=True)
def clean_runs(monkeypatch):
    from services import ask_runs
    monkeypatch.setattr(ask_runs,'_RUNS',{})


def test_cancel_and_mode_change_block_next_attempt(owners,monkeypatch):
    from services import ask_runs,llm_runtime,model_catalog
    a,b,_=owners
    steps=[]
    gate=asyncio.Event()
    async def events(q,*args):
        yield {'type':'thought','text':'部分内容'}
        await gate.wait()
        llm_runtime.check_cancelled()
        steps.append('tool')
        yield {'type':'answer','text':'late'}
    monkeypatch.setattr(ask_runs,'ask_stock_stream',events)
    async def exercise():
        await configure(a,b)
        with user_scope(b):
            r1,r2=await asyncio.gather(ask_runs.start('same'),ask_runs.start('same'))
            assert r1.id==r2.id
            assert ask_runs.cancel(r1.id)
            gate.set()
            await asyncio.gather(r1.task,return_exceptions=True)
            assert not r1.answered and r1.status=='cancelled'
            assert not steps
            await model_catalog.save_preferences(local_only=True,scenes={'ask':['cloud']})
            with pytest.raises(LLMError):
                await llm_runtime.generate(CallContext(b,'ask','old'),ModelRequest(messages=[]),['cloud'])
    asyncio.run(exercise())


def test_fallback_is_a_separate_complete_version(owners,monkeypatch):
    from services import ask_runs,model_catalog,llm_runtime
    a,b,_=owners
    async def stream(question,*args):
        selected=llm_runtime._candidate.get()
        if selected=='cloud':
            yield {'type':'partial','text':'未完成的第一版'}
            yield {'type':'error','error':'synthetic failure'}
        else:yield {'type':'answer','text':'独立完整第二版'}
    monkeypatch.setattr(ask_runs,'ask_stock_stream',stream)
    async def exercise():
        await configure(a,b)
        with user_scope(a):
            await model_catalog.save_model(dict(id='local',protocol='ollama',endpoint='http://localhost:11434',model='synthetic',local=True,capabilities=['text','tools']),expected_version=0)
        with user_scope(b):
            await model_catalog.save_preferences(local_only=False,scenes={'ask':['cloud','local']})
            run=await ask_runs.start('versions')
            await run.task
            from database import get_ask_session
            msgs=(await get_ask_session(run.session_id,user_id=b))['messages']
            assert [m['content'] for m in msgs[1:]]==['未完成的第一版','独立完整第二版']
            assert run.answered
    asyncio.run(exercise())


def test_local_queue_timeout_never_starts_a_cloud_answer_version(owners,monkeypatch):
    from services import ask_runs,model_catalog as c,llm_runtime as r
    a,b,_=owners
    seen=[]
    async def stream(*args):
        seen.append(r._candidate.get())
        yield {'type':'error','error':'本地等待超时','code':'queue_timeout'}
    monkeypatch.setattr(ask_runs,'ask_stock_stream',stream)
    async def exercise():
        await configure(a,b)
        with user_scope(a):await c.save_model(dict(id='local',protocol='ollama',endpoint='http://localhost:11434',model='synthetic',local=True,capabilities=['text','tools']),expected_version=0)
        with user_scope(b):
            await c.save_preferences(local_only=False,scenes={'ask':['local','cloud']})
            run=await ask_runs.start('queue timeout')
            await run.task
            assert run.status=='failed'
    asyncio.run(exercise())
    assert seen==['local']


def test_task_deadline_is_terminal_and_does_not_restart(owners,monkeypatch):
    from services import ask_runs
    a,b,_=owners
    started=[]
    async def events(*args):
        started.append(1)
        await asyncio.Event().wait()
        yield {'type':'answer','text':'never'}
    monkeypatch.setattr(ask_runs,'ask_stock_stream',events)
    monkeypatch.setattr(ask_runs,'_TASK_LIMIT_SEC',0.01)
    async def exercise():
        with user_scope(b):
            run=await ask_runs.start('deadline')
            await run.task
            assert run.status=='timeout' and run.done and not run.answered
            assert started==[1]
            assert any(e.get('code')=='task_timeout' for e in run.events)
    asyncio.run(exercise())
