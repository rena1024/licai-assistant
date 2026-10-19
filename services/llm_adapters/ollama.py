import json
from services.llm_types import ModelResult, Usage, LLMError


def encode(model, request):
    from services.llm_adapters.chat import chat_messages, chat_tools
    messages = chat_messages(request.messages)
    tool_names={b['id']:b['name'] for m in request.messages if isinstance(m.get('content'),list) for b in m['content'] if b.get('type')=='tool_use'}
    if request.system:
        messages.insert(0,{'role':'system','content':request.system})
    # Ollama images 独立传 base64；不能把远程 image URL 当作本机输入。
    for message in messages:
        if message.get('role')=='tool':
            call_id=message.pop('tool_call_id',None)
            if call_id not in tool_names:raise LLMError('tool_call_missing')
            message['tool_name']=tool_names[call_id]
        if isinstance(message.get('content'),list):
            texts,images = [],[]
            for block in message['content']:
                if block.get('type')=='text':texts.append(block['text'])
                elif block.get('type')=='image_url':
                    url = block['image_url']['url']
                    if not url.startswith('data:') or ';base64,' not in url:
                        raise LLMError('unsupported_image','本地图像必须为 base64 数据')
                    images.append(url.split(';base64,',1)[1])
                else:raise LLMError('unsupported_content')
            message['content']='\n'.join(texts)
            if images:message['images']=images
        if 'tool_calls' in message:
            for tool in message['tool_calls']:
                tool['function']['arguments']=json.loads(tool['function']['arguments'])
    payload = {'model':model.model,'messages':messages,'stream':False,
               'options':{'num_predict':request.output_limit,'num_ctx':model.context_limit}}
    if request.tools:payload['tools']=chat_tools(request.tools)
    if request.response_schema:payload['format']=request.response_schema
    return payload,'/api/chat',{}


def decode(data):
    if data.get('error'):raise LLMError('provider_error','本地模型返回错误')
    message=data.get('message') or {}
    blocks=[]
    if message.get('content'):blocks.append({'type':'text','text':message['content']})
    for index,call in enumerate(message.get('tool_calls') or []):
        f=call['function']
        blocks.append({'type':'tool_use','id':call.get('id') or f'ollama-{index}','name':f['name'],'input':f['arguments']})
    usage = Usage(input_tokens=data['prompt_eval_count'],output_tokens=data['eval_count']) if 'prompt_eval_count' in data and 'eval_count' in data else None
    return ModelResult(blocks,usage,'tool_use' if any(b['type']=='tool_use' for b in blocks) else data.get('done_reason','stop'),tool_calls=[b for b in blocks if b['type']=='tool_use'])
