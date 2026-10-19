"""当前用户消息和渠道；所有资源由服务端登录身份限定。"""
import time
from fastapi import APIRouter,HTTPException,Request,Query
from pydantic import BaseModel,ConfigDict
from services import notifications as n,notification_delivery as d,runtime_state

router=APIRouter(prefix='/api/notifications',tags=['notifications'])


class Active(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    channel_id: str | None


class Resend(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    channel_id: str
    confirmed: bool


@router.get('')
async def messages(limit: int=Query(50,ge=1,le=100),before: int | None=None,cursor: str | None=None):
    return {'messages':await n.list_messages(limit=limit,before=before,cursor=cursor)}


@router.post('/{identifier}/read')
async def read(identifier: str):
    if not await n.mark_read(identifier):
        raise HTTPException(404,'消息不存在')
    return {'ok':True}


@router.get('/channels')
async def channels():
    return {'channels':await d.channels(),'notification_hold':bool(runtime_state.read_state().get('notification_hold',False))}


@router.put('/channels')
async def save_channel(request: Request):
    try:
        data=await request.json()
        version=data.pop('expected_version')
        if type(version) is not int or version<0:
            raise ValueError()
        return await d.save_channel(data,expected_version=version)
    except (ValueError,KeyError,TypeError,AttributeError):
        raise HTTPException(422,'渠道配置无效，请检查必填项、TLS 地址和版本；凭证不回显') from None


@router.put('/active')
async def active(data: Active):
    try:
        await d.activate(data.channel_id)
        return {'ok':True}
    except ValueError as exc:
        raise HTTPException(409,str(exc)) from None


@router.delete('/channels/{identifier}')
async def delete_channel(identifier: str):
    try:
        await d.delete_channel(identifier)
        return {'ok':True}
    except ValueError as exc:
        raise HTTPException(409,str(exc)) from None


@router.post('/test')
async def test_channel(data: Resend):
    # 保存不会发消息；显式测试仅发往页面确认的当前渠道。
    from services.user_context import require_user_id
    from database import get_db
    import uuid
    if data.confirmed is not True:
        raise HTTPException(422,'请确认测试接收渠道')
    db=await get_db()
    try:
        await db.execute('BEGIN IMMEDIATE')
        active=await (await db.execute('SELECT channel_id FROM notification_active WHERE user_id=?',(require_user_id(),))).fetchone()
        if not active or active[0]!=data.channel_id:
            raise HTTPException(409,'当前渠道已变化，请刷新')
        identifier=await n.insert_event(db,require_user_id(),'test',uuid.uuid4().hex,'通知测试','由本人主动触发的渠道测试')
        await db.commit()
        return {'id':identifier,'state':'pending'}
    finally:
        await db.close()


@router.post('/{identifier}/resend')
async def resend(identifier: str,data: Resend):
    try:
        await d.resend(identifier,data.channel_id,confirmed=data.confirmed)
        return {'ok':True}
    except ValueError as exc:
        raise HTTPException(409,str(exc)) from None


@router.post('/restore-confirmation')
async def restore_confirmation():
    from database import get_db
    from services.accounts import _require_admin
    from services.user_context import require_user_id
    from services import audit
    db=await get_db()
    try:
        await _require_admin(db,require_user_id())
        runtime_state.update_state(notification_hold=False)
        await audit.record(db,action='configuration_changed',outcome='success',created_at=int(time.time()),actor_id=require_user_id())
        await db.commit()
        return {'notification_hold':False}
    finally:
        await db.close()


class Retention(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    days: int


@router.get('/policy')
async def policy():
    return await n.retention()


@router.put('/policy')
async def save_policy(data: Retention):
    try:
        return await n.set_retention(data.days)
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from None
