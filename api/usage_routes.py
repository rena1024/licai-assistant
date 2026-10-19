from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from services import budget, usage, runtime_state
from services.llm_types import Usage
from database import get_db
from services.accounts import _require_admin
from services.user_context import require_user_id

router = APIRouter(prefix='/api/usage', tags=['usage'])


class Limits(BaseModel):
    model_config = ConfigDict(extra='forbid')
    subject: int = Field(ge=0)
    day: str | None
    month: str | None


class Reconciliation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    usage: Usage
    reason: str = Field(min_length=5,max_length=500)


@router.get('')
async def report(subject: int | None = None):
    return await usage.report(subject=subject)


@router.put('/limits')
async def limits(data: Limits):
    try:
        await budget.set_limits(**data.model_dump())
        return {'saved':True}
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from None


@router.post('/reconcile/{attempt_id}')
async def reconcile(attempt_id: str, data: Reconciliation):
    try:
        await usage.reconcile(attempt_id,data.usage,data.reason)
        return {'reconciled':True}
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from None


class CloudConfirmation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reason: str = Field(min_length=5,max_length=500)


class Policy(BaseModel):
    model_config = ConfigDict(extra='forbid')
    detail_days: int = Field(ge=1,le=365)
    warning_threshold: int = Field(ge=1,le=99)
    local_concurrency: int = Field(default=1,ge=1,le=8)


@router.get('/policy')
async def get_policy():
    db=await get_db()
    try:
        await _require_admin(db,require_user_id())
        rows=await (await db.execute("SELECT key,value FROM app_config WHERE key IN ('usage_retention_days','usage_alert_threshold','local_llm_concurrency')")).fetchall()
        values=dict(rows)
        return {'detail_days':int(values.get('usage_retention_days','7')),'warning_threshold':int(values.get('usage_alert_threshold','80')),'local_concurrency':int(values.get('local_llm_concurrency','1'))}
    finally:await db.close()


@router.put('/policy')
async def policy(data: Policy):
    db=await get_db()
    try:
        await db.execute('BEGIN IMMEDIATE')
        await _require_admin(db,require_user_id())
        await db.executemany('INSERT INTO app_config VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', [('usage_retention_days',str(data.detail_days)),('usage_alert_threshold',str(data.warning_threshold)),('local_llm_concurrency',str(data.local_concurrency))])
        from services import audit,accounts
        await audit.record(db,action='configuration_changed',outcome='success',created_at=accounts._now(None),actor_id=require_user_id())
        await db.commit()
        from services.local_queue import queue
        queue.configure(data.local_concurrency)
        return {'saved':True}
    finally:await db.close()


@router.get('/cloud-state')
async def cloud_state():
    state = runtime_state.read_state()
    return {'held':state.get('cloud_hold',True),'last_restore_at':state.get('last_restore_at')}


@router.post('/cloud-confirmation')
async def confirm_cloud(data: CloudConfirmation):
    db = await get_db()
    try:
        await _require_admin(db,require_user_id())
        if await (await db.execute("SELECT 1 FROM llm_attempts WHERE state IN ('reserved','unknown') LIMIT 1")).fetchone():
            raise HTTPException(409,'仍有在途或待核实费用，请先核对')
        # 恢复后的金额以管理员核对后的四项额度及持久占用为准，不能清零历史。
        await db.execute('INSERT INTO llm_reconciliations(attempt_id,actor_id,reason,created_at) VALUES (NULL,?,?,strftime(\'%s\',\'now\'))', (require_user_id(),data.reason))
        await db.execute('DELETE FROM llm_service_holds')
        await db.commit()
        runtime_state.update_state(cloud_hold=False)
        return {'held':False}
    finally:
        await db.close()
