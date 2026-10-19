import json
from services.llm_adapters.chat import chat_messages,chat_tools
from services.llm_types import ModelResult,Usage,LLMError


def encode(model,request):
    messages=chat_messages(request.messages)
    if request.system:messages.insert(0,{'role':'system','content':request.system})
    payload={'model':model.model,'messages':messages,'max_tokens':request.output_limit,'stream':False}
    if request.tools:payload['tools']=chat_tools(request.tools)
    if request.response_schema:payload['response_format']={'type':'json_schema','json_schema':{'name':'result','schema':request.response_schema,'strict':True}}
    return payload,'/chat/completions',{'Authorization':'Bearer '+model.api_key.get_secret_value()}


def decode(data):
    if data.get('error') or not data.get('choices'):raise LLMError('provider_error','模型服务返回错误')
    choice=data['choices'][0];message=choice.get('message') or {}
    blocks=[]
    if message.get('content'):blocks.append({'type':'text','text':message['content']})
    for call in message.get('tool_calls') or []:
        function=call['function']
        blocks.append({'type':'tool_use','id':call['id'],'name':function['name'],'input':json.loads(function['arguments'])})
    raw=data.get('usage') or {};usage=None
    if 'prompt_tokens' in raw and 'completion_tokens' in raw:
        usage=Usage(input_tokens=raw['prompt_tokens'],output_tokens=raw['completion_tokens'],
                    cache_read_tokens=(raw.get('prompt_tokens_details') or {}).get('cached_tokens',0),
                    reasoning_tokens=(raw.get('completion_tokens_details') or {}).get('reasoning_tokens',0))
    calls=[b for b in blocks if b['type']=='tool_use']
    return ModelResult(blocks,usage,'tool_use' if calls else 'max_tokens' if choice.get('finish_reason')=='length' else 'end_turn',provider_request_id=data.get('id',''),tool_calls=calls)
