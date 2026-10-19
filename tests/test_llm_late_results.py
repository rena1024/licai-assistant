import asyncio
import threading
import pytest
from tests.test_ownership_portfolio import owners
from tests.test_llm_attempts import configure
from services.llm_types import CallContext,ModelRequest
from services.user_context import user_scope


def test_cancelled_inflight_attempt_still_settles_and_never_tries_backup(owners,monkeypatch):
    from services import llm_runtime as r,budget
    a,b,_=owners
    started,release,stop=threading.Event(),threading.Event(),threading.Event()
    seen=[]
    def send(*args):
        seen.append('send');started.set();assert release.wait(5)
        return {'content':[{'type':'text','text':'late'}],'usage':{'input_tokens':10,'output_tokens':1}}
    monkeypatch.setattr(r,'send_http',send)
    async def exercise():
        await configure(a,b)
        with user_scope(b):
            token=r._cancel.set(stop)
            try:task=asyncio.create_task(r.generate(CallContext(b,'ask','late'),ModelRequest(messages=[])))
            finally:r._cancel.reset(token)
            assert await asyncio.to_thread(started.wait,5)
            stop.set();task.cancel();release.set()
            with pytest.raises(asyncio.CancelledError):await task
            assert (await budget.summary())['user']['day']['spent']==120
            assert seen==['send']
    asyncio.run(exercise())


def test_switching_to_local_blocks_cloud_retry_and_uses_only_selected_local(owners,monkeypatch):
    from services import llm_runtime as r,model_catalog as c
    a,b,_=owners
    started,release=threading.Event(),threading.Event()
    seen=[]
    def send(model,*args):
        seen.append(model.id)
        if model.id=='cloud':
            started.set();assert release.wait(5)
            raise r.TransportError('overloaded',known_unbilled=True)
        return {'message':{'content':'local'},'prompt_eval_count':1,'eval_count':1}
    monkeypatch.setattr(r,'send_http',send)
    async def exercise():
        await configure(a,b)
        with user_scope(a):
            await c.save_model(dict(id='local',protocol='ollama',endpoint='http://localhost:11434',model='synthetic',local=True),expected_version=0)
        with user_scope(b):
            await c.save_preferences(local_only=False,scenes={'ask':['cloud','local']})
            task=asyncio.create_task(r.generate(CallContext(b,'ask','switch'),ModelRequest(messages=[])))
            assert await asyncio.to_thread(started.wait,5)
            await c.save_preferences(local_only=True,scenes={'ask':['cloud','local']})
            release.set()
            assert (await task).model_id=='local'
    asyncio.run(exercise())
    assert seen==['cloud','local']
