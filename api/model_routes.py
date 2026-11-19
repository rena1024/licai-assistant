"""模型开放配置与当前登录用户的场景偏好。"""
from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel, ConfigDict, Field
from services import model_catalog as catalog
from services.llm_types import LLMError

router = APIRouter(prefix='/api/models', tags=['models'])


def _capability_error_reason(error):
    # 仅展示受控错误码对应的说明，避免回显厂商响应中的凭证或请求内容。
    messages = {
        'cloud_held': '云端调用已暂停，请在“用量与预算”核对后确认开放',
        'model_unavailable': '模型已停用或未开放所测能力，请检查模型服务设置',
        'budget_unconfigured': '全局日额度和月额度均须大于 0；任一留空或为 0 都会阻止云端模型测试，请在“用量与预算”补齐',
        'budget_exhausted': '可用额度不足，请检查用量与预算',
        'usage_unknown': '存在用量待核对的调用，请先在用量与预算中处理',
        'provider_transport_error': '无法连接模型服务，请检查服务地址和网络',
        'private_endpoint': '云端服务域名解析为非公网地址，已阻止请求；请检查代理 Fake-IP 或 DNS 配置，确保该域名解析为真实公网地址',
        'provider_http_error': '模型服务拒绝请求，请检查凭证、模型 ID 和服务状态',
    }
    reasons = list(dict.fromkeys(messages[code] for code in (error.failure_codes or (error.code,)) if code in messages))
    return '能力测试未通过：' + '；'.join(reasons) if reasons else '能力测试未通过，请检查配置及用量状态'


class Preferences(BaseModel):
    model_config = ConfigDict(extra='forbid')
    local_only: bool
    scenes: dict[str, list[str]]
    expected_version: int = Field(ge=0)
    automatic_ai_enabled: bool | None = None


@router.get('')
async def models():
    return {'models': await catalog.list_models(), 'scenes': catalog.SCENES}


@router.put('')
async def save_model(request: Request):
    # 不将含密钥的输入交给默认校验错误正文。
    try:
        data = await request.json()
        version = data.pop('expected_version')
        if type(version) is not int or version < 0:
            raise ValueError()
        return await catalog.save_model(data, expected_version=version)
    except LLMError as exc:
        raise HTTPException(409, str(exc)) from None
    except (ValueError, KeyError, TypeError, AttributeError):
        raise HTTPException(422, '模型配置无效，请检查端点、价格、凭证和版本') from None


@router.get('/preferences')
async def preferences():
    return await catalog.preferences()


@router.get('/activity')
async def model_activity(request_id: str = Query(default='', pattern=r'^([a-f0-9]{32})?$'),
                         task_id: str = Query(default='', max_length=128)):
    from fastapi.responses import JSONResponse
    from services import llm_activity
    return JSONResponse({'calls': llm_activity.snapshot(request_id=request_id, task_id=task_id)},
                        headers={'Cache-Control': 'no-store'})


@router.put('/preferences')
async def save_preferences(data: Preferences):
    try:
        return await catalog.save_preferences(**data.model_dump())
    except LLMError as exc:
        raise HTTPException(409, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


class CapabilityTest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    capability: str = Field(pattern='^(text|tools|image|structured)$')


class ModelVersion(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_version: int = Field(ge=1, strict=True)


class ModelState(ModelVersion):
    enabled: bool = Field(strict=True)


@router.patch('/{identifier}')
async def set_model_enabled(identifier: str, data: ModelState):
    try:
        return await catalog.set_model_enabled(identifier, **data.model_dump())
    except LLMError as exc:
        raise HTTPException(409, str(exc)) from None
    except ValueError:
        raise HTTPException(422, '模型配置无效，请先编辑并补齐配置') from None


@router.delete('/{identifier}')
async def delete_model(identifier: str, data: ModelVersion):
    try:
        return await catalog.delete_model(identifier, **data.model_dump())
    except LLMError as exc:
        raise HTTPException(409, str(exc)) from None


@router.post('/{identifier}/test')
async def test_capability(identifier: str, data: CapabilityTest):
    from services.accounts import _require_admin
    from services.user_context import require_user_id
    from database import get_db
    from services.llm_types import CallContext, ModelRequest
    from services.llm_runtime import generate
    import json, time, uuid
    db = await get_db()
    try:
        await _require_admin(db,require_user_id())
    finally:
        await db.close()
    model,version = await catalog.private_model(identifier)
    capability = data.capability
    request = ModelRequest(messages=[{'role':'user','content':'Reply with OK.'}], output_limit=256,capability={capability})
    if capability == 'tools':
        request.messages=[{'role':'user','content':'Call the ping tool with value OK. Do not reply with text.'}]
        request.tools=[{'name':'ping','description':'Return a test value','input_schema':{'type':'object','properties':{'value':{'type':'string'}},'required':['value']}}]
    elif capability == 'structured':
        request.response_schema={'type':'object','properties':{'ok':{'type':'boolean'}},'required':['ok'],'additionalProperties':False}
        request.messages=[{'role':'user','content':'Return {"ok":true}.'}]
    elif capability == 'image':
        request.messages=[{'role':'user','content':[{'type':'text','text':'Describe this pixel image briefly.'},
            {'type':'image','source':{'type':'base64','media_type':'image/png','data':'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aH1sAAAAASUVORK5CYII='}}]}]
    success=False
    reason=''
    try:
        result=await generate(CallContext(None,'diagnostic',uuid.uuid4().hex,'diagnostic'),request,[identifier])
        text=''.join(b.get('text','') for b in result.content if b.get('type')=='text')
        success=bool(text)
        if capability=='tools':success=any(t.get('name')=='ping' and t.get('input',{}).get('value')=='OK' for t in result.tool_calls)
        elif capability=='structured':success=json.loads(text)=={'ok':True}
        if not success:reason='模型响应未满足能力合同'
    except LLMError as exc:
        reason=_capability_error_reason(exc)
    except ValueError:
        reason='能力测试未通过，请检查配置及用量状态'
    db=await get_db()
    try:
        await db.execute('INSERT INTO llm_model_checks VALUES (?,?,?,?,?,?) ON CONFLICT(model_id,version,capability) DO UPDATE SET success=excluded.success,reason=excluded.reason,created_at=excluded.created_at', (identifier,version,capability,int(success),reason,int(time.time())))
        await db.commit()
    finally:await db.close()
    if not success:raise HTTPException(409,reason)
    return {'success':True,'model_id':identifier,'version':version,'capability':capability}
