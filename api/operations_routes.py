"""管理员只查看恢复点元信息与运行状态，不浏览备份／日志业务正文。"""
import asyncio
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel,ConfigDict
from services.user_context import require_user_id
from services import backup,restore,audit,runtime_state,logging_config

router=APIRouter(prefix='/api/admin',tags=['operations'])


class RestoreIn(BaseModel):
    model_config=ConfigDict(extra='forbid')
    confirmation: str


class TraceIn(BaseModel):
    model_config=ConfigDict(extra='forbid')
    enabled: bool


@router.get('/backups')
async def backups():
    return {'backups':await asyncio.to_thread(backup.list_backups)}


@router.post('/backups')
async def create_backup():
    try:
        result=await asyncio.to_thread(backup.create_backup,kind='manual')
        await audit.management_event('backup_created',actor_id=require_user_id())
        logging_config.record_event(event='backup_created',outcome='success',elapsed_ms=0,actor_id=require_user_id())
        return result
    except backup.BackupError as exc:
        logging_config.record_event(event='backup_failed',outcome='failure',elapsed_ms=0,actor_id=require_user_id())
        raise HTTPException(503,{'code':exc.code,'message':str(exc)}) from None


@router.post('/backups/{identifier}/restore')
async def restore_point(identifier: str,data: RestoreIn):
    if data.confirmation!=identifier:raise HTTPException(400,'请确认要恢复的备份标识')
    actor=require_user_id()
    try:
        result=await restore.restore_backup(identifier,actor_id=actor)
        with runtime_state.runtime_scope():
            # 恢复后整数账号ID可能不同；审计不借旧ID冒认新账号。
            await audit.management_event('restore_completed')
        logging_config.record_event(event='restore_completed',outcome='success',elapsed_ms=0)
        return result
    except backup.BackupError as exc:
        logging_config.record_event(event='restore_failed',outcome='failure',elapsed_ms=0)
        raise HTTPException(400,{'code':exc.code,'message':str(exc)}) from None


@router.get('/operations')
async def operations():
    state=runtime_state.read_state()
    return {'maintenance':bool(state.get('maintenance')),'cloud_hold':bool(state.get('cloud_hold',True)),
            'backup_success_at':state.get('backup_success_at'),'backup_error':state.get('backup_error'),
            'trace':logging_config.safe_metadata()}


@router.post('/trace')
async def trace(data: TraceIn):
    result=logging_config.set_trace(data.enabled)
    await audit.management_event('trace_changed',actor_id=require_user_id())
    logging_config.record_event(event='trace_enabled' if data.enabled else 'trace_disabled',outcome='success',elapsed_ms=0,actor_id=require_user_id())
    return result
