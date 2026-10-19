import json
from pathlib import Path
import pytest
from services.model_catalog import ModelConfig
from services.llm_types import ModelRequest


def test_protocol_fixtures_normalize_tools_usage_and_errors():
    from services.llm_adapters import openai,deepseek,qwen
    from services.budget import cost_micro
    from tests.test_budget_ledger import PRICE
    fixtures=[(openai, json.loads((Path(__file__).parent/'fixtures'/'llm'/'openai.json').read_text())),
              (deepseek, json.loads((Path(__file__).parent/'fixtures'/'llm'/'deepseek.json').read_text())),
              (qwen, json.loads((Path(__file__).parent/'fixtures'/'llm'/'qwen.json').read_text()))]
    for module,data in fixtures:
        result=module.decode(data)
        assert result.tool_calls[0]['input']=={'code':'1'}
        assert result.usage.input_tokens==100 and result.usage.output_tokens==50
        assert result.usage.cache_read_tokens==30 and result.usage.reasoning_tokens==20
        assert cost_micro(PRICE,result.usage)==1730
        assert module.decode({**data,'usage':{'new_unknown_field':999}}).usage is None
        with pytest.raises(Exception):module.decode({'error':{'message':'sensitive vendor error'}})


def test_responses_request_is_native_and_tools_preserve_ids():
    from services.llm_adapters import openai
    from tests.test_budget_ledger import PRICE
    model=ModelConfig(id='o',protocol='openai',endpoint='https://api.openai.com',model='synthetic',api_key='test',price=PRICE)
    request=ModelRequest(messages=[{'role':'assistant','content':[{'type':'tool_use','id':'c','name':'quote','input':{}}]},
                                   {'role':'user','content':[{'type':'tool_result','tool_use_id':'c','content':'data'}]}],tools=[{'name':'quote','input_schema':{'type':'object'}}])
    payload,path,headers=openai.encode(model,request)
    assert path=='/v1/responses'
    assert payload['input'][1]['type']=='function_call_output'
    assert payload['input'][1]['call_id']=='c'
