from services.llm_types import ModelResult, Usage


def encode(model, request):
    payload = {'model':model.model,'messages':request.messages,'max_tokens':request.output_limit}
    if request.system:
        payload['system'] = request.system
    if request.tools:
        # 厂商内置的付费工具不属于用户工具合同。
        if any(t.get('type') not in {None,'custom'} for t in request.tools):
            raise ValueError('不支持厂商内置工具')
        payload['tools'] = [{k:v for k,v in t.items() if k != 'type'} for t in request.tools]
    if request.response_schema:
        payload['output_config'] = {'format':{'type':'json_schema','schema':request.response_schema}}
    return payload,'/v1/messages',{'x-api-key':model.api_key.get_secret_value(),'anthropic-version':'2023-06-01'}


def decode(data):
    raw = data.get('usage') or {}
    usage = None
    if 'input_tokens' in raw and 'output_tokens' in raw:
        read,write = raw.get('cache_read_input_tokens',0),raw.get('cache_creation_input_tokens',0)
        usage = Usage(input_tokens=raw['input_tokens']+read+write,output_tokens=raw['output_tokens'],cache_read_tokens=read,cache_write_tokens=write,
                      cache_write_1h_tokens=(raw.get('cache_creation') or {}).get('ephemeral_1h_input_tokens',0),
                      reasoning_tokens=(raw.get('output_tokens_details') or {}).get('thinking_tokens',0))
    blocks = data.get('content') or []
    return ModelResult(blocks,usage,data.get('stop_reason','stop'),provider_request_id=data.get('id',''),tool_calls=[b for b in blocks if b.get('type')=='tool_use'])
