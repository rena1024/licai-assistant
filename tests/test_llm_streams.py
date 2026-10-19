import json
import pytest
from services.llm_adapters.stream import collect
from services.llm_adapters import adapter


def test_native_stream_fixtures_normalize_and_require_terminal_events():
    fixtures=[
        ('anthropic',[{'type':'message_start','message':{'id':'a','usage':{'input_tokens':2}}},
                      {'type':'content_block_start','index':0,'content_block':{'type':'text','text':''}},
                      {'type':'content_block_delta','index':0,'delta':{'type':'text_delta','text':'ok'}},
                      {'type':'message_delta','delta':{'stop_reason':'end_turn'},'usage':{'output_tokens':1}}, {'type':'message_stop'}]),
        ('openai',[{'type':'response.completed','response':{'id':'r','status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':'ok'}]}], 'usage':{'input_tokens':2,'output_tokens':1}}}]),
        ('ollama',[{'message':{'content':'ok'},'done':False},{'message':{'content':''},'done':True,'prompt_eval_count':2,'eval_count':1}]),
        ('gemini',[{'candidates':[{'content':{'parts':[{'text':'ok'}]},'finishReason':'STOP'}],'usageMetadata':{'promptTokenCount':2,'candidatesTokenCount':1}}]),
    ]
    for protocol,events in fixtures:
        lines=['data: '+json.dumps(e) for e in events]
        result=adapter(protocol).decode(collect(protocol,lines))
        assert result.content[0]['text']=='ok'
        assert result.usage.input_tokens==2 and result.usage.output_tokens==1
        with pytest.raises(Exception):collect(protocol,[])


@pytest.mark.parametrize('protocol',['deepseek','qwen','openai_compatible'])
def test_chat_stream_tool_arguments_are_reassembled(protocol):
    lines=[{'id':'r','choices':[{'delta':{'tool_calls':[{'index':0,'id':'call1','function':{'name':'ping','arguments':'{"v":'}}]}}]},
           {'choices':[{'delta':{'tool_calls':[{'index':0,'function':{'arguments':'1}'}}]},'finish_reason':'tool_calls'}]},
           {'choices':[],'usage':{'prompt_tokens':2,'completion_tokens':1}}]
    data=collect(protocol,['data: '+json.dumps(e) for e in lines]+['data: [DONE]'])
    result=adapter(protocol).decode(data)
    assert result.tool_calls[0]['input']=={'v':1}
    assert result.usage.input_tokens==2
