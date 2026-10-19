"""已证实共享的聊天消息与自定义工具格式；各厂商计量分别映射。"""
import json
from services.llm_types import LLMError


def chat_tools(tools):
    result=[]
    for tool in tools:
        if tool.get('type') not in {None,'custom'}:
            raise LLMError('unsupported_tool','不支持厂商内置工具')
        result.append({'type':'function','function':{'name':tool['name'],'description':tool.get('description',''),'parameters':tool.get('input_schema',{'type':'object','properties':{}})}})
    return result


def chat_messages(messages):
    result=[]
    for message in messages:
        content=message.get('content','')
        if isinstance(content,str):
            result.append({'role':message['role'],'content':content})
            continue
        blocks,calls,results,images=[],[],[],[]
        reasoning=''
        for b in content:
            kind=b.get('type')
            if kind=='text':blocks.append({'type':'text','text':b['text']})
            elif kind=='image':
                s=b['source']
                if s.get('type')!='base64':raise LLMError('unsupported_image')
                blocks.append({'type':'image_url','image_url':{'url':f"data:{s['media_type']};base64,{s['data']}"}})
            elif kind=='tool_use':calls.append({'id':b['id'],'type':'function','function':{'name':b['name'],'arguments':json.dumps(b['input'])}})
            elif kind=='tool_result':
                content=b['content']
                if isinstance(content,list):
                    texts=[]
                    for part in content:
                        if part.get('type')=='text':texts.append(part['text'])
                        elif part.get('type')=='image':
                            source=part['source']
                            if source.get('type')!='base64':raise LLMError('unsupported_image')
                            images.append({'type':'image_url','image_url':{'url':f"data:{source['media_type']};base64,{source['data']}"}})
                        else:raise LLMError('unsupported_content')
                    content='\n'.join(texts) or '工具返回图像，见后续图像消息'
                results.append({'role':'tool','tool_call_id':b['tool_use_id'],'content':content if isinstance(content,str) else json.dumps(content)})
            elif kind=='reasoning' and b.get('provider')=='deepseek':reasoning+=b.get('text','')
            else:raise LLMError('unsupported_content','消息包含不支持的内容类型')
        if blocks or calls:
            item={'role':message['role'],'content':blocks or None}
            if calls:item['tool_calls']=calls
            if reasoning:item['reasoning_content']=reasoning
            result.append(item)
        result.extend(results)
        # 先完成本轮全部 tool 响应，再发送图像，避免打断工具调用配对。
        if images:result.append({'role':'user','content':images})
    return result
