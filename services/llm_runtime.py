"""所有物理推理请求的授权、预留、发送和结算入口。"""
import asyncio
import json
import threading
import uuid
import socket
import ipaddress
import time
from urllib.parse import urlsplit
from contextvars import ContextVar
import requests
from services import model_catalog, budget, runtime_state
from services.llm_types import CallContext, ModelRequest, ModelResult, Usage, LLMError
from services.llm_adapters import adapter
from services.user_context import require_user_id

_scene = ContextVar('llm_scene',default='ask')
_task = ContextVar('llm_task',default=None)
_cancel = ContextVar('llm_cancel',default=None)
_background = ContextVar('llm_background',default=False)
_candidate = ContextVar('llm_candidate',default=None)
_workers = set()
_previous_failures = ContextVar('llm_previous_failures',default='')


class TransportError(LLMError):
    def __init__(self,code,*,known_unbilled=False):
        super().__init__(code,'云端地址不能指向内网或本机' if code=='private_endpoint' else '模型服务请求失败；请查看用量状态后重试')
        self.known_unbilled=known_unbilled


def validate_ollama_metadata(data):
    if (data.get('remote_host') or data.get('remote_model') or not data.get('model_info')
            or not data.get('details') or data.get('details',{}).get('remote_host')):
        raise LLMError('remote_ollama','无法证明此 Ollama 模型在本机运行，拒绝推理')


def _read_body(response,*,deadline,maximum=8*1024*1024):
    chunks=[];size=0
    for chunk in response.iter_content(chunk_size=16384):
        if time.monotonic()>deadline:raise TransportError('provider_total_timeout')
        size+=len(chunk)
        if size>maximum:raise TransportError('provider_response_too_large')
        chunks.append(chunk)
    return b''.join(chunks)


def send_http(model,payload,path,headers):
    """单次发送；不使用环境代理、HTTP 重定向或库级自动重试。"""
    started=time.monotonic()
    with requests.Session() as session:
        session.trust_env=False
        u=urlsplit(model.endpoint)
        addresses=socket.getaddrinfo(u.hostname,u.port or (443 if u.scheme=='https' else 80),type=socket.SOCK_STREAM)
        if not addresses:raise TransportError('endpoint_unresolved',known_unbilled=True)
        ips=[ipaddress.ip_address(a[4][0]) for a in addresses]
        if not model.local and any(not ip.is_global for ip in ips):
            raise TransportError('private_endpoint',known_unbilled=True) from None
        if model.local and u.hostname!='host.docker.internal' and any(not ip.is_loopback for ip in ips):
            raise LLMError('local_endpoint_invalid','本地端点解析到非本机地址')
        # 固定已校验 IP，TLS 仍验证原域名，阻止校验后再次解析产生 DNS 重绑定。
        original_host=u.hostname
        if u.scheme=='https':
            class PinnedAdapter(requests.adapters.HTTPAdapter):
                def init_poolmanager(self,*args,**kwargs):
                    kwargs.update(assert_hostname=original_host,server_hostname=original_host)
                    return super().init_poolmanager(*args,**kwargs)
                def proxy_manager_for(self,proxy,**kwargs):
                    kwargs.update(assert_hostname=original_host,server_hostname=original_host)
                    return super().proxy_manager_for(proxy,**kwargs)
            session.mount('https://',PinnedAdapter(max_retries=0))
        ip=str(ips[0]);host=f'[{ip}]' if ':' in ip else ip
        base=f'{u.scheme}://{host}:{u.port or (443 if u.scheme=="https" else 80)}{u.path.rstrip("/")}'
        headers={**headers,'Host':u.netloc}
        from services.proxy_config import get_proxy
        proxy=get_proxy()
        if proxy and not model.local:session.proxies={'http':proxy,'https':proxy}
        try:
            if model.local:
                metadata=session.post(base+'/api/show',json={'model':model.model},headers={'Host':u.netloc},timeout=(10,20),allow_redirects=False,stream=True)
                if metadata.status_code!=200:
                    metadata.close()
                    raise LLMError('local_model_unverified','无法验证本地模型元信息')
                with metadata:validate_ollama_metadata(json.loads(_read_body(metadata,deadline=time.monotonic()+20,maximum=1024*1024)))
            response=session.post(base+path,json=payload,headers=headers,
                                  timeout=(10,180 if model.local else 120),allow_redirects=False,stream=True)
            if not 200 <= response.status_code < 300:
                response.close()
                raise TransportError('provider_http_error')
            with response:body=_read_body(response,deadline=started+(600 if model.local else 300))
            if 'event-stream' in response.headers.get('content-type','') or 'ndjson' in response.headers.get('content-type',''):
                from services.llm_adapters.stream import collect
                try:data=collect(model.protocol,body.splitlines())
                except (LLMError,ValueError):
                    raise TransportError('incomplete_provider_stream') from None
            else:data=json.loads(body)
            if not isinstance(data,dict):raise TransportError('invalid_response')
            return data
        except (requests.RequestException,ValueError):
            raise TransportError('provider_transport_error') from None


def check_cancelled():
    from services.task_context import ensure_active_sync
    ensure_active_sync()
    stop=_cancel.get()
    if stop is not None and stop.is_set():raise LLMError('cancelled','任务已取消')


async def _authorize(context,identifier,request):
    check_cancelled()
    db=await __import__('database').get_db()
    try:await budget._context_owner(db,context)
    finally:await db.close()
    if context.purpose=='user':
        allowed=await model_catalog.candidates(context.scene,request.required_capabilities())
        if identifier not in {m['id'] for m in allowed}:raise LLMError('model_not_selected','模型未选、已停用或能力不足')
    model,version=await model_catalog.private_model(identifier)
    if not model.enabled or not request.required_capabilities() <= model.capabilities:
        raise LLMError('model_unavailable')
    if not model.local and runtime_state.read_state().get('cloud_hold',True):
        raise LLMError('cloud_held','云端调用暂停，请管理员核对并确认')
    return model,version


def _reservation(model,request):
    # UTF-8 字节上界覆盖工具、系统提示和多模态；图像按整个上下文预留。
    inputs=len(json.dumps(request.model_dump(mode='json'),ensure_ascii=False).encode())+1024
    if 'image' in request.required_capabilities():inputs=model.context_limit-request.output_limit
    if inputs+request.output_limit > model.context_limit:
        raise LLMError('context_limit','请求超过模型上下文限制')
    p=model.price
    # 缓存写入可能比普通输入贵，预留选择最高输入价格。
    snapshot=p.model_dump(mode='json')
    snapshot['input']=str(max(p.input,p.cache_write,p.cache_read,p.cache_write_1h or 0))
    maximum=budget.cost_micro(snapshot,Usage(input_tokens=inputs,output_tokens=request.output_limit))
    return (maximum*110+99)//100


async def _execute(context,request,identifier,attempt):
    started=time.monotonic()
    model,version=await _authorize(context,identifier,request)
    module=adapter(model.protocol)
    payload,path,headers=module.encode(model,request)
    rid=None
    if not model.local:
        rid=await budget.reserve_attempt(context,identifier,model.price.model_dump(mode='json'),_reservation(model,request),attempt)
    else:
        from services.usage import start_local
        rid=await start_local(context,identifier,attempt)
    try:
        # SQLite 预留完成后再次验证，配置版本变化则撤销尚未发出的预留。
        current,current_version=await _authorize(context,identifier,request)
        if current_version != version:raise LLMError('config_changed','模型配置变化，请重试')
        data=send_http(model,payload,path,headers)
        try:result=module.decode(data)
        except (ValueError,KeyError,TypeError,AttributeError):
            raise LLMError('invalid_provider_response','模型响应格式不符合协议，已保留待核实费用') from None
    except Exception as exc:
        known=isinstance(exc,LLMError) and not isinstance(exc,TransportError)
        # decode 异常发生在发送后，用量不能当作已知零。
        known=known and 'data' not in locals()
        known=known or (isinstance(exc,TransportError) and exc.known_unbilled)
        if model.local:
            from services.usage import settle_local
            await settle_local(rid,None)
        else:await budget.settle_attempt(rid,Usage(input_tokens=0,output_tokens=0) if known else None)
        await _attempt_metadata(rid,started,version,getattr(exc,'code','provider_error'))
        raise
    if model.local:
        from services.usage import settle_local
        await settle_local(rid,result.usage)
    else:
        try:await budget.settle_attempt(rid,result.usage,result.provider_request_id)
        except ValueError:
            await budget.settle_attempt(rid,None,result.provider_request_id)
            raise LLMError('usage_invalid','服务用量无法核对，已暂停') from None
    result.model_id=identifier
    await _attempt_metadata(rid,started,version,result.finish_reason)
    check_cancelled()
    if not result.content:raise LLMError('empty_response','模型未返回可用内容')
    return result


async def _attempt_metadata(rid,started,version,outcome):
    db=await __import__('database').get_db()
    try:
        row=await (await db.execute('SELECT metadata FROM llm_usage_details WHERE attempt_id=?',(rid,))).fetchone()
        if row:
            value=json.loads(row[0])
            value.update(elapsed_ms=int((time.monotonic()-started)*1000),model_version=version,outcome=str(outcome)[:100],fallback_reason=_previous_failures.get())
            await db.execute('UPDATE llm_usage_details SET metadata=? WHERE attempt_id=?',(json.dumps(value),rid))
            await db.commit()
    finally:await db.close()


def _worker(context,request,identifier,attempt):
    key,_=runtime_state.acquire_connection()
    try:
        model,_=asyncio.run(_authorize(context,identifier,request))
        if model.local:
            from services.local_queue import queue,configure_from_db
            asyncio.run(configure_from_db())
            with queue.slot(context,stop=_cancel.get()):
                return asyncio.run(_execute(context,request,identifier,attempt))
        return asyncio.run(_execute(context,request,identifier,attempt))
    finally:runtime_state.release_connection(key)


async def generate(context:CallContext, request:ModelRequest, candidate_ids=None):
    if context.purpose=='user' and context.owner_id != require_user_id():raise LLMError('invalid_owner')
    selected=await model_catalog.candidates(context.scene,request.required_capabilities()) if context.purpose=='user' else []
    allowed=[m['id'] for m in selected]
    if _candidate.get() is not None:
        allowed=[i for i in allowed if i==_candidate.get()]
    if context.purpose=='diagnostic':allowed=list(candidate_ids or [])
    elif candidate_ids is not None:
        # 调用方可以缩小候选，但不可增加或更改用户的顺序。
        allowed=[i for i in allowed if i in candidate_ids]
    if not allowed:raise LLMError('no_eligible_model','没有已选择且符合模式与能力的模型')
    failures=[]
    for identifier in allowed:
        for retry in range(2):
            check_cancelled()
            reason_token=_previous_failures.set('；'.join(failures))
            try:work=asyncio.create_task(asyncio.to_thread(_worker,context,request,identifier,uuid.uuid4().hex))
            finally:_previous_failures.reset(reason_token)
            _workers.add(work)
            try:
                result=await asyncio.shield(work)
                result.fallback_reason='；'.join(failures)
                return result
            except asyncio.CancelledError:
                # 线程中的在途请求必须完成结算；取消后不再发布或尝试备用。
                stop=_cancel.get()
                if stop:stop.set()
                while not work.done():
                    try:await asyncio.shield(work)
                    except asyncio.CancelledError:continue
                    except Exception:break
                raise
            except LLMError as exc:
                failures.append(exc.code)
                if exc.code in {'cancelled','queue_timeout','queue_full'}:raise
                if isinstance(exc,TransportError) and exc.known_unbilled and retry==0:
                    await asyncio.sleep(1)
                    continue
                break
            finally:
                _workers.discard(work)
    raise LLMError('all_candidates_failed','所选模型不可用：'+'；'.join(failures))
