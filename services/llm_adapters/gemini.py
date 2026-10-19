"""Gemini 原生 generateContent；工具签名随对话保留，不出现在用量页面。"""
import json
from urllib.parse import quote
from services.llm_types import ModelResult,Usage,LLMError


def encode(model,request):
    contents=[]
    names={}
    for message in request.messages:
        raw=message.get('content','')
        parts=[]
        if isinstance(raw,str):parts=[{'text':raw}]
        else:
            for block in raw:
                kind=block.get('type')
                if kind=='text':
                    part={'text':block['text']}
                    if block.get('signature'):part['thoughtSignature']=block['signature']
                    parts.append(part)
                elif kind=='image':
                    s=block['source']
                    if s.get('type')!='base64':raise LLMError('unsupported_image')
                    parts.append({'inlineData':{'mimeType':s['media_type'],'data':s['data']}})
                elif kind=='tool_use':
                    names[block['id']]=block['name']
                    part={'functionCall':{'name':block['name'],'args':block['input']}}
                    if block.get('signature'):part['thoughtSignature']=block['signature']
                    parts.append(part)
                elif kind=='tool_result':
                    if block['tool_use_id'] not in names:raise LLMError('tool_call_missing')
                    result=block['content']
                    images=[]
                    if isinstance(result,list):
                        texts=[]
                        for part in result:
                            if part.get('type')=='text':texts.append(part['text'])
                            elif part.get('type')=='image':
                                source=part['source']
                                if source.get('type')!='base64':raise LLMError('unsupported_image')
                                images.append({'inlineData':{'mimeType':source['media_type'],'data':source['data']}})
                            else:raise LLMError('unsupported_content')
                        result='\n'.join(texts)
                    parts.append({'functionResponse':{'name':names[block['tool_use_id']],'response':{'result':result}}})
                    parts.extend(images)
                elif kind=='opaque' and block.get('protocol')=='gemini':parts.append(block['part'])
                else:raise LLMError('unsupported_content')
        contents.append({'role':'model' if message['role']=='assistant' else 'user','parts':parts})
    payload={'contents':contents,'generationConfig':{'maxOutputTokens':request.output_limit}}
    if request.system:payload['systemInstruction']={'parts':[{'text':request.system}]}
    if request.tools:
        from services.llm_adapters.chat import chat_tools
        payload['tools']=[{'functionDeclarations':[tool['function'] for tool in chat_tools(request.tools)]}]
    if request.response_schema:payload['generationConfig'].update(responseMimeType='application/json',responseJsonSchema=request.response_schema)
    return payload,f'/v1beta/models/{quote(model.model,safe="")}:generateContent',{'x-goog-api-key':model.api_key.get_secret_value()}


def decode(data):
    if data.get('error') or not data.get('candidates'):raise LLMError('provider_error','Gemini 未返回可用候选')
    candidate=data['candidates'][0];blocks=[]
    for index,part in enumerate((candidate.get('content') or {}).get('parts',[])):
        if part.get('text') and not part.get('thought'):
            text={'type':'text','text':part['text']}
            if part.get('thoughtSignature'):text['signature']=part['thoughtSignature']
            blocks.append(text)
        elif part.get('thought') or ('thoughtSignature' in part and 'functionCall' not in part):
            blocks.append({'type':'opaque','protocol':'gemini','part':part})
        if 'functionCall' in part:
            f=part['functionCall']
            block={'type':'tool_use','id':f.get('id') or f'gemini-{index}-{f["name"]}','name':f['name'],'input':f.get('args',{})}
            if part.get('thoughtSignature'):block['signature']=part['thoughtSignature']
            blocks.append(block)
    raw=data.get('usageMetadata') or {};usage=None
    if 'promptTokenCount' in raw and 'candidatesTokenCount' in raw:
        thoughts=raw.get('thoughtsTokenCount',0)
        usage=Usage(input_tokens=raw['promptTokenCount'],output_tokens=raw['candidatesTokenCount']+thoughts,
                    cache_read_tokens=raw.get('cachedContentTokenCount',0),reasoning_tokens=thoughts)
    calls=[b for b in blocks if b['type']=='tool_use']
    return ModelResult(blocks,usage,'tool_use' if calls else 'max_tokens' if candidate.get('finishReason')=='MAX_TOKENS' else 'end_turn',provider_request_id=data.get('responseId',''),tool_calls=calls)
