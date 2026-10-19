import asyncio
import pytest
from tests.test_ownership_portfolio import owners
from tests.test_budget_ledger import PRICE
from services.user_context import user_scope
from services.llm_types import CallContext, ModelRequest, LLMError


async def configure(a,b):
    from services import model_catalog as c,budget,runtime_state
    with user_scope(a):
        await c.save_model(dict(id='cloud',protocol='anthropic',endpoint='https://api.anthropic.com',model='synthetic',api_key='synthetic',price=PRICE,capabilities=['text','tools']),expected_version=0)
        await budget.set_limits(0,day='100',month='100')
        await budget.set_limits(b,day='100',month='100')
    with user_scope(b):
        await c.save_preferences(local_only=False,scenes={'ask':['cloud']})
    runtime_state.update_state(cloud_hold=False)


def test_each_retry_is_authorized_and_accounted(owners,monkeypatch):
    from services import llm_runtime as runtime
    from database import get_db
    a,b,_=owners
    seen=[]
    def send(model,payload,path,headers):
        seen.append(payload)
        if len(seen)==1:
            raise runtime.TransportError('overloaded', known_unbilled=True)
        return {'content':[{'type':'text','text':'ok'}],'stop_reason':'end_turn','usage':{'input_tokens':10,'output_tokens':1}}
    monkeypatch.setattr(runtime,'send_http',send)
    async def exercise():
        await configure(a,b)
        with user_scope(b):
            result=await runtime.generate(CallContext(b,'ask','t'),ModelRequest(messages=[{'role':'user','content':'hi'}]),['cloud'])
            assert result.content[0]['text']=='ok'
            db=await get_db()
            try:
                rows=await (await db.execute('SELECT state FROM llm_attempts')).fetchall()
                assert len(rows)==2 and all(r['state']=='settled' for r in rows)
            finally:await db.close()
    asyncio.run(exercise())


def test_local_mode_and_zero_budget_make_zero_http(owners,monkeypatch):
    from services import llm_runtime as runtime,model_catalog,budget
    a,b,_=owners
    def no_send(*args):raise AssertionError('禁止发出请求')
    monkeypatch.setattr(runtime,'send_http',no_send)
    async def exercise():
        await configure(a,b)
        with user_scope(a):await budget.set_limits(b,day='0',month='100')
        with user_scope(b):
            with pytest.raises(LLMError):
                await runtime.generate(CallContext(b,'ask','t'),ModelRequest(messages=[]),['cloud'])
            await model_catalog.save_preferences(local_only=True,scenes={'ask':['cloud']})
            with pytest.raises(LLMError):
                await runtime.generate(CallContext(b,'ask','t'),ModelRequest(messages=[]),['cloud'])
    asyncio.run(exercise())
