import asyncio
import pytest
from tests.test_ownership_portfolio import owners
from tests.test_llm_attempts import configure
from services.llm_types import CallContext,ModelRequest,LLMError
from services.user_context import user_scope


def test_capability_failure_uses_only_selected_eligible_backup(owners,monkeypatch):
    from services import llm_runtime as runtime,model_catalog as catalog
    a,b,_=owners
    seen=[]
    def send(model,payload,path,headers):
        seen.append(model.id)
        return {'message':{'content':'local'},'prompt_eval_count':1,'eval_count':1}
    monkeypatch.setattr(runtime,'send_http',send)
    async def exercise():
        await configure(a,b)
        with user_scope(a):
            await catalog.save_model(dict(id='local',protocol='ollama',endpoint='http://localhost:11434',model='synthetic',local=True,capabilities=['text','image']),expected_version=0)
        with user_scope(b):
            await catalog.save_preferences(local_only=False,scenes={'ask':['cloud','local']})
            result=await runtime.generate(CallContext(b,'ask','image'),ModelRequest(messages=[{'role':'user','content':'image task'}],capability={'image'}))
            assert result.model_id=='local' and seen==['local']
            await catalog.save_preferences(local_only=True,scenes={})
            with pytest.raises(LLMError):await runtime.generate(CallContext(b,'ask','empty'),ModelRequest(messages=[]))
            assert seen==['local']
    asyncio.run(exercise())


def test_gemini_signature_and_reasoning_usage_are_preserved():
    from services.llm_adapters import gemini
    from services.model_catalog import ModelConfig
    from tests.test_budget_ledger import PRICE
    raw={'candidates':[{'content':{'parts':[{'functionCall':{'name':'quote','args':{'code':'1'}},'thoughtSignature':'opaque-signature'}]},'finishReason':'STOP'}],
         'usageMetadata':{'promptTokenCount':100,'candidatesTokenCount':50,'thoughtsTokenCount':20,'cachedContentTokenCount':30}}
    result=gemini.decode(raw)
    assert result.usage.output_tokens==70 and result.usage.reasoning_tokens==20
    assert result.tool_calls[0]['signature']=='opaque-signature'
    model=ModelConfig(id='g',protocol='gemini',endpoint='https://generativelanguage.googleapis.com',model='synthetic',api_key='test',price=PRICE,capabilities=['text','tools'])
    request=ModelRequest(messages=[{'role':'assistant','content':result.content}])
    payload,path,headers=gemini.encode(model,request)
    assert payload['contents'][0]['parts'][0]['thoughtSignature']=='opaque-signature'
    assert 'test' not in path
