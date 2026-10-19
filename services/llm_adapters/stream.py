"""将厂商流事件收敛为完整响应；缺少终止事件不能伪装成功或零用量。"""
import json
from services.llm_types import LLMError


def collect(protocol,lines):
    events=[];done=False
    for line in lines:
        if isinstance(line,bytes):line=line.decode('utf-8')
        if not line or line.startswith(('event:',':','id:')):continue
        raw=line[5:].strip() if line.startswith('data:') else line
        if raw=='[DONE]':done=True;continue
        data=json.loads(raw)
        if data.get('error') or data.get('type')=='error':raise LLMError('provider_stream_error','模型流返回错误')
        events.append(data)
    if protocol=='openai':
        terminal=next((e.get('response') for e in reversed(events) if e.get('type') in {'response.completed','response.incomplete'}),None)
        if terminal:return terminal
    elif protocol=='anthropic':
        result={'content':[],'usage':{}}
        blocks={}
        for event in events:
            kind=event.get('type')
            if kind=='message_start':
                result.update(event['message']);result['usage']=dict(result.get('usage') or {})
            elif kind=='content_block_start':blocks[event['index']]=dict(event['content_block'])
            elif kind=='content_block_delta':
                block=blocks[event['index']];delta=event['delta']
                if 'text' in delta:block['text']=block.get('text','')+delta['text']
                if 'partial_json' in delta:block['_json']=block.get('_json','')+delta['partial_json']
                if 'thinking' in delta:block['thinking']=block.get('thinking','')+delta['thinking']
                if 'signature' in delta:block['signature']=block.get('signature','')+delta['signature']
            elif kind=='message_delta':
                result.update(event.get('delta') or {});result['usage'].update(event.get('usage') or {})
            elif kind=='message_stop':done=True
        if done:
            for block in blocks.values():
                if '_json' in block:block['input']=json.loads(block.pop('_json'))
            result['content']=[blocks[i] for i in sorted(blocks)]
            return result
    elif protocol in {'deepseek','qwen','openai_compatible'}:
        result={'choices':[{'message':{'content':''},'finish_reason':None}]}
        message=result['choices'][0]['message'];calls={}
        for event in events:
            if 'usage' in event:result['usage']=event['usage']
            if event.get('id'):result['id']=event['id']
            for choice in event.get('choices',[]):
                delta=choice.get('delta') or {}
                message['content']+=delta.get('content') or ''
                if delta.get('reasoning_content'):message['reasoning_content']=message.get('reasoning_content','')+delta['reasoning_content']
                for call in delta.get('tool_calls') or []:
                    target=calls.setdefault(call['index'],{'id':'','type':'function','function':{'name':'','arguments':''}})
                    for key in ('id',):
                        if call.get(key):target[key]+=call[key]
                    for key,value in (call.get('function') or {}).items():
                        if key in {'name','arguments'} and value:target['function'][key]+=value
                if choice.get('finish_reason'):result['choices'][0]['finish_reason']=choice['finish_reason']
        if done:
            if calls:message['tool_calls']=[calls[i] for i in sorted(calls)]
            return result
    elif protocol=='ollama':
        result={'message':{'content':''}}
        for event in events:
            msg=event.get('message') or {}
            result['message']['content']+=msg.get('content','')
            if msg.get('tool_calls'):result['message'].setdefault('tool_calls',[]).extend(msg['tool_calls'])
            if event.get('done'):
                done=True
                result.update({k:v for k,v in event.items() if k!='message'})
        if done:return result
    elif protocol=='gemini':
        parts=[];result={}
        for event in events:
            if event.get('usageMetadata'):result['usageMetadata']=event['usageMetadata']
            for c in event.get('candidates',[]):
                parts.extend((c.get('content') or {}).get('parts',[]))
                if c.get('finishReason'):done=True;result['finishReason']=c['finishReason']
        if done:return {'candidates':[{'content':{'parts':parts},'finishReason':result['finishReason']}],**({'usageMetadata':result['usageMetadata']} if 'usageMetadata' in result else {})}
    raise LLMError('incomplete_stream','模型流在终止事件前中断，用量保留待核实')
