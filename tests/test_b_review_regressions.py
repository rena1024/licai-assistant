"""B 部分最终统一 review 的确定性复现与回归。"""
import asyncio
import sqlite3
import pytest
from tests.test_ownership_portfolio import owners
from tests.test_llm_attempts import configure
from tests.test_budget_ledger import PRICE
from services.user_context import user_scope
from services.llm_types import CallContext,ModelRequest


def test_local_diagnostic_uses_actor_queue_identity_without_personal_budget(owners,monkeypatch):
    from services import model_catalog as c,llm_runtime as r
    a,b,_=owners
    seen=[]
    def send(*args):
        seen.append(1)
        return {'message':{'content':'OK'},'prompt_eval_count':1,'eval_count':1}
    monkeypatch.setattr(r,'send_http',send)
    async def exercise():
        with user_scope(a):
            await c.save_model(dict(id='local',protocol='ollama',endpoint='http://localhost:11434',model='synthetic',local=True),expected_version=0)
            result=await r.generate(CallContext(None,'diagnostic','test','diagnostic'),ModelRequest(messages=[{'role':'user','content':'OK'}]),['local'])
            assert result.content[0]['text']=='OK'
    asyncio.run(exercise())
    assert seen==[1]


def test_invalid_protocol_tool_json_is_accounted_and_tries_selected_backup(owners,monkeypatch):
    from services import model_catalog as c,llm_runtime as r
    from database import get_db
    a,b,_=owners
    seen=[]
    def send(model,*args):
        seen.append(model.id)
        if model.id=='bad':return {'choices':[{'message':{'tool_calls':[{'id':'1','function':{'name':'ping','arguments':'{broken'}}]}}], 'usage':{'prompt_tokens':1,'completion_tokens':1}}
        return {'message':{'content':'OK'},'prompt_eval_count':1,'eval_count':1}
    monkeypatch.setattr(r,'send_http',send)
    async def exercise():
        await configure(a,b)
        with user_scope(a):
            await c.save_model(dict(id='bad',protocol='openai_compatible',endpoint='https://synthetic.example/v1',model='synthetic',api_key='synthetic',price=PRICE,capabilities=['text','tools']),expected_version=0)
            await c.save_model(dict(id='local',protocol='ollama',endpoint='http://localhost:11434',model='synthetic',local=True,capabilities=['text','tools']),expected_version=0)
        with user_scope(b):
            await c.save_preferences(local_only=False,scenes={'ask':['bad','local']})
            result=await r.generate(CallContext(b,'ask','t'),ModelRequest(messages=[{'role':'user','content':'OK'}]))
            assert result.model_id=='local'
            db=await get_db()
            try:
                row=await (await db.execute("SELECT state,reserved FROM llm_attempts WHERE model_id='bad'")).fetchone()
                assert row['state']=='unknown' and row['reserved']>0
            finally:await db.close()
    asyncio.run(exercise())
    assert seen==['bad','local']


def test_nested_tool_image_requires_image_capability_and_full_context_reservation():
    from services.model_catalog import ModelConfig
    from services.llm_runtime import _reservation
    from services.budget import cost_micro
    from services.llm_types import Usage
    request=ModelRequest(messages=[{'role':'user','content':[{'type':'tool_result','tool_use_id':'x','content':[{'type':'image','source':{'type':'base64','media_type':'image/png','data':'synthetic'}}]}]}])
    assert 'image' in request.required_capabilities()
    model=ModelConfig(id='c',protocol='anthropic',endpoint='https://synthetic.example',model='test',api_key='synthetic',price=PRICE)
    high_price={**PRICE,'input':'12'}
    maximum=cost_micro(high_price,Usage(input_tokens=model.context_limit-request.output_limit,output_tokens=request.output_limit))
    assert _reservation(model,request)==(maximum*110+99)//100


def test_restoring_pre_b_database_recreates_local_queue_and_interrupts_old_jobs(owners):
    from services.llm_recovery import preserve_finance
    a,b,path=owners
    restored_path=path.parent/'restored.db'
    with sqlite3.connect(path) as current,sqlite3.connect(restored_path) as restored:
        current.backup(restored)
        for name, in restored.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'llm_%'").fetchall():
            restored.execute(f'DROP TABLE {name}')
        preserve_finance(current,restored)
        assert restored.execute("SELECT 1 FROM sqlite_master WHERE name='llm_local_jobs'").fetchone()
        restored.execute('INSERT INTO llm_local_jobs VALUES (?,?,?,?,?,?)',('old',b,'old-task',0,'running',1))
        preserve_finance(current,restored)
        assert restored.execute("SELECT status FROM llm_local_jobs WHERE id='old'").fetchone()[0]=='interrupted'


@pytest.mark.parametrize('protocol',['anthropic','openai','deepseek','qwen','gemini','ollama','openai_compatible'])
def test_nested_tool_images_are_encoded_as_images(protocol):
    from services.llm_adapters import adapter
    from services.model_catalog import ModelConfig
    import json
    local=protocol=='ollama'
    model=ModelConfig(id='test',protocol=protocol,endpoint='http://localhost:11434' if local else 'https://synthetic.example',model='test',local=local,api_key='' if local else 'synthetic',price=None if local else PRICE,capabilities=['text','tools','image'])
    image={'type':'image','source':{'type':'base64','media_type':'image/png','data':'synthetic-image'}}
    request=ModelRequest(messages=[{'role':'assistant','content':[{'type':'tool_use','id':'chart1','name':'chart','input':{}}]},
                                   {'role':'user','content':[{'type':'tool_result','tool_use_id':'chart1','content':[{'type':'text','text':'chart result'},image]}]}])
    payload,_,_=adapter(protocol).encode(model,request)
    def image_keys(value):
        if isinstance(value,dict):
            return (value.get('type') in {'image','image_url','input_image'} or 'inlineData' in value or bool(value.get('images'))
                    or any(image_keys(v) for v in value.values()))
        return isinstance(value,list) and any(image_keys(v) for v in value)
    assert image_keys(payload),json.dumps(payload)
