"""兼容签名必须委托受控运行时；旧隐藏重试／OAuth／CLI 回退已移除。"""
import pytest
from services import llm_client,llm_runtime
from services.llm_types import ModelResult,Usage,LLMError


@pytest.mark.parametrize('scene',['ask','briefing','fundamental','scanner','multi_lens','rules','news','portfolio','market','sector'])
def test_compatibility_wrapper_preserves_scene_and_never_selects_hardcoded_model(monkeypatch,scene):
    seen=[]
    async def generate(context,request,candidate_ids=None):
        seen.append((context,request,candidate_ids))
        return ModelResult([{'type':'text','text':'synthetic'}],Usage(input_tokens=1,output_tokens=1),model_id='user-selected')
    monkeypatch.setattr(llm_runtime,'generate',generate)
    assert llm_client.call_claude('prompt','system','old-hardcoded-model',123,scene=scene)=='synthetic'
    context,request,candidates=seen[0]
    assert context.scene==scene and context.owner_id==1
    assert request.output_limit==123 and request.system=='system'
    assert candidates is None


def test_empty_text_does_not_automatically_multiply_output_budget(monkeypatch):
    seen=[]
    async def generate(*args):
        seen.append(args)
        return ModelResult([],Usage(input_tokens=1,output_tokens=2048),'max_tokens')
    monkeypatch.setattr(llm_runtime,'generate',generate)
    with pytest.raises(LLMError,match='未完成'):
        llm_client.call_claude('prompt')
    assert len(seen)==1


def test_old_connection_test_requires_explicit_diagnostic():
    with pytest.raises(LLMError):llm_client.test_connection()
    assert not llm_client.get_llm_config()['using_oauth_fallback']
