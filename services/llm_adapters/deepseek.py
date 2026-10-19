"""DeepSeek 聊天协议，命中缓存已包含在 prompt_tokens。"""
from services.llm_adapters import openai_compatible as chat
from services.llm_types import LLMError


def encode(model,request):
    if request.response_schema:
        raise LLMError('unsupported_capability','该 DeepSeek 适配器不支持严格 JSON Schema 输出')
    payload,_,headers=chat.encode(model,request)
    return payload,'/chat/completions',headers


def decode(data):
    result=chat.decode(data)
    raw=data.get('usage') or {}
    if result.usage:
        result.usage.cache_read_tokens=raw.get('prompt_cache_hit_tokens',0)
    reasoning=((data.get('choices') or [{}])[0].get('message') or {}).get('reasoning_content')
    if reasoning:
        result.content.insert(0,{'type':'reasoning','provider':'deepseek','text':reasoning})
    return result
