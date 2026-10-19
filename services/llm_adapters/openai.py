import json
from services.llm_types import ModelResult,Usage,LLMError


def encode(model,request):
    items=[]
    for message in request.messages:
        raw=message.get('content','')
        if isinstance(raw,str):
            items.append({'role':message['role'],'content':raw});continue
        blocks=[]
        for block in raw:
            kind=block.get('type')
            if kind=='text':blocks.append({'type':'output_text' if message['role']=='assistant' else 'input_text','text':block['text']})
            elif kind=='image':
                s=block['source']
                if s.get('type')!='base64':raise LLMError('unsupported_image')
                blocks.append({'type':'input_image','image_url':f"data:{s['media_type']};base64,{s['data']}"})
            elif kind=='tool_use':items.append({'type':'function_call','call_id':block['id'],'name':block['name'],'arguments':json.dumps(block['input'])})
            elif kind=='tool_result':
                content=block['content']
                if isinstance(content,list):
                    mapped=[]
                    for part in content:
                        if part.get('type')=='text':mapped.append({'type':'input_text','text':part['text']})
                        elif part.get('type')=='image':
                            s=part['source']
                            if s.get('type')!='base64':raise LLMError('unsupported_image')
                            mapped.append({'type':'input_image','image_url':f"data:{s['media_type']};base64,{s['data']}"})
                        else:raise LLMError('unsupported_content')
                    content=mapped
                elif not isinstance(content,str):content=json.dumps(content)
                items.append({'type':'function_call_output','call_id':block['tool_use_id'],'output':content})
            elif kind=='opaque' and block.get('protocol')=='openai':items.append(block['item'])
            else:raise LLMError('unsupported_content')
        if blocks:items.append({'role':message['role'],'content':blocks})
    payload={'model':model.model,'input':items,'max_output_tokens':request.output_limit,'store':False,'include':['reasoning.encrypted_content']}
    if request.system:payload['instructions']=request.system
    if request.tools:
        from services.llm_adapters.chat import chat_tools
        payload['tools']=[{'type':'function',**tool['function']} for tool in chat_tools(request.tools)]
    if request.response_schema:payload['text']={'format':{'type':'json_schema','name':'result','schema':request.response_schema,'strict':True}}
    return payload,'/v1/responses',{'Authorization':'Bearer '+model.api_key.get_secret_value()}


def decode(data):
    if data.get('error') or data.get('status')=='failed':raise LLMError('provider_error','模型服务返回错误')
    content=[]
    for item in data.get('output',[]):
        if item.get('type')=='message':
            for block in item.get('content',[]):
                if block.get('type')=='output_text':content.append({'type':'text','text':block['text']})
        elif item.get('type')=='function_call':
            content.append({'type':'tool_use','id':item['call_id'],'name':item['name'],'input':json.loads(item['arguments'])})
        elif item.get('type')=='reasoning':content.append({'type':'opaque','protocol':'openai','item':item})
    raw=data.get('usage') or {}
    usage=None
    if 'input_tokens' in raw and 'output_tokens' in raw:
        usage=Usage(input_tokens=raw['input_tokens'],output_tokens=raw['output_tokens'],cache_read_tokens=(raw.get('input_tokens_details') or {}).get('cached_tokens',0),reasoning_tokens=(raw.get('output_tokens_details') or {}).get('reasoning_tokens',0))
    calls=[b for b in content if b['type']=='tool_use']
    reason='tool_use' if calls else 'max_tokens' if data.get('status')=='incomplete' else 'end_turn'
    return ModelResult(content,usage,reason,provider_request_id=data.get('id',''),tool_calls=calls)
