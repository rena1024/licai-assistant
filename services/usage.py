"""只读计量元数据；聚合及未结算依据不随明细清理。"""
import time
import uuid
import json
from database import get_db
from services.accounts import _require_admin
from services.user_context import require_user_id
from services import budget


async def start_local(context,model_id,key):
    db=await get_db()
    try:
        owner=await budget._context_owner(db,context)
        rid=uuid.uuid4().hex
        day,month,at=budget.periods()
        await db.execute('INSERT INTO llm_attempts(id,owner_id,user_uuid,purpose,task_id,scene,model_id,idempotency_key,day,month,price,reserved,state,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                         (rid,context.owner_id,owner['uuid'] if owner else None,context.purpose,context.task_id,context.scene,model_id,key,day,month,'{}',0,'local_pending',at))
        await db.commit()
        return rid
    finally:await db.close()


async def settle_local(rid,usage):
    db=await get_db()
    try:
        await db.execute('BEGIN IMMEDIATE')
        row=await (await db.execute('SELECT * FROM llm_attempts WHERE id=?',(rid,))).fetchone()
        if row['state']=='settled':return
        inputs,outputs=(usage.input_tokens,usage.output_tokens) if usage else (None,None)
        await db.execute("UPDATE llm_attempts SET state='settled',cost=0,input_tokens=?,output_tokens=? WHERE id=?",(inputs,outputs,rid))
        await db.execute('INSERT OR REPLACE INTO llm_usage_details VALUES (?,?,?)',(rid,row['created_at'],json.dumps({'local':True,'usage_unknown':usage is None})))
        for subject in [0]+([row['owner_id']] if row['owner_id'] is not None else []):
            await db.execute('INSERT INTO llm_usage_monthly VALUES (?,?,?,?,1,?,?) ON CONFLICT(subject,month,model_id,scene) DO UPDATE SET calls=calls+1,input_tokens=input_tokens+excluded.input_tokens,output_tokens=output_tokens+excluded.output_tokens', (subject,row['month'],row['model_id'],row['scene'],inputs or 0,outputs or 0))
        await db.commit()
    finally:await db.close()


async def report(*, subject=None, limit=100):
    uid = require_user_id()
    target = uid if subject is None else subject
    db = await get_db()
    try:
        if target != uid:
            await _require_admin(db,uid)
        # 全局视图显式选 0；普通用户不得查看其他人的预算或明细。
        condition, args = ('1=1', ()) if target == 0 else ('a.owner_id=?', (target,))
        details = await (await db.execute(f'''SELECT a.id,a.owner_id,a.purpose,a.task_id,a.scene,a.model_id,
            a.state,a.created_at,a.cost,a.reserved,a.price,a.original_cost,a.input_tokens,a.output_tokens,
            a.cache_read_tokens,a.cache_write_tokens,a.reasoning_tokens,a.cache_write_1h_tokens,d.metadata
            FROM llm_attempts a JOIN llm_usage_details d ON d.attempt_id=a.id
            WHERE {condition} ORDER BY a.created_at DESC,a.id LIMIT ?''', (*args,min(200,max(1,limit))))).fetchall()
        monthly = await (await db.execute('SELECT * FROM llm_usage_monthly WHERE subject=? ORDER BY month DESC', (target,))).fetchall()
        alerts = await (await db.execute('SELECT * FROM llm_budget_alerts WHERE subject=? ORDER BY period DESC', (target,))).fetchall()
        pending = await (await db.execute(f"SELECT a.id,a.model_id,a.state,a.reserved,a.created_at FROM llm_attempts a WHERE {condition} AND a.state!='settled'", args)).fetchall()
        return {'budget':await budget.summary(subject=target), 'monthly':[dict(r) for r in monthly],
                'alerts':[dict(r) for r in alerts], 'details':[dict(r) for r in details],
                'pending':[dict(r) for r in pending], 'estimated':True}
    finally:
        await db.close()


async def purge_details(*, now=None):
    # 仅短期表；不触碰余额、汇总、提醒和财务依据。
    db = await get_db()
    try:
        row = await (await db.execute("SELECT value FROM app_config WHERE key='usage_retention_days'")).fetchone()
        days = int(row[0]) if row else 7
        await db.execute('DELETE FROM llm_usage_details WHERE created_at<?', (int(now if now is not None else time.time())-days*86400,))
        await db.commit()
    finally:
        await db.close()


async def reconcile(reservation_id, usage, reason):
    if not reason or not 5 <= len(reason.strip()) <= 500:
        raise ValueError('人工核对需要填写 5～500 字理由')
    db = await get_db()
    try:
        await _require_admin(db,require_user_id())
        row = await (await db.execute("SELECT COALESCE(service_id,model_id) AS service_id,state FROM llm_attempts WHERE id=?", (reservation_id,))).fetchone()
        if not row or row['state'] != 'unknown':
            raise ValueError('只能核对待核实尝试')
    finally:
        await db.close()
    await budget.settle_attempt(reservation_id,usage,audit_reason=reason.strip())
