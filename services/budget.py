"""单事务预留四项额度；账本、余额和阈值去重独立于短期明细。"""
import json
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_CEILING
from database import get_db
from services.accounts import _require_admin
from services.user_context import require_user_id
from services.llm_types import LLMError, Usage
from services.model_catalog import Price

TZ = timezone(timedelta(hours=8))
SCHEMA = '''
CREATE TABLE IF NOT EXISTS llm_budget_limits (
 subject INTEGER PRIMARY KEY, day INTEGER, month INTEGER
);
CREATE TABLE IF NOT EXISTS llm_budget_totals (
 subject INTEGER NOT NULL, kind TEXT NOT NULL, period TEXT NOT NULL,
 spent INTEGER NOT NULL DEFAULT 0, reserved INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(subject,kind,period)
);
CREATE TABLE IF NOT EXISTS llm_attempts (
 id TEXT PRIMARY KEY, owner_id INTEGER, user_uuid TEXT, purpose TEXT NOT NULL,
 task_id TEXT NOT NULL, scene TEXT NOT NULL, model_id TEXT NOT NULL, service_id TEXT,
 idempotency_key TEXT NOT NULL UNIQUE, day TEXT NOT NULL, month TEXT NOT NULL,
 price TEXT NOT NULL, reserved INTEGER NOT NULL, cost INTEGER, original_cost TEXT,
 state TEXT NOT NULL, created_at INTEGER NOT NULL, request_id TEXT,
 input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER, cache_write_tokens INTEGER, reasoning_tokens INTEGER, cache_write_1h_tokens INTEGER
);
CREATE TABLE IF NOT EXISTS llm_service_holds (model_id TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS llm_reconciliations (
 id INTEGER PRIMARY KEY AUTOINCREMENT, attempt_id TEXT, actor_id INTEGER NOT NULL,
 reason TEXT NOT NULL, created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS llm_budget_alerts (
 subject INTEGER NOT NULL, kind TEXT NOT NULL, period TEXT NOT NULL, threshold INTEGER NOT NULL,
 PRIMARY KEY(subject,kind,period,threshold)
);
CREATE TABLE IF NOT EXISTS llm_usage_details (
 attempt_id TEXT PRIMARY KEY, created_at INTEGER NOT NULL, metadata TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS llm_usage_monthly (
 subject INTEGER NOT NULL, month TEXT NOT NULL, model_id TEXT NOT NULL, scene TEXT NOT NULL,
 calls INTEGER NOT NULL DEFAULT 0, input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(subject,month,model_id,scene)
);
'''


def periods(now=None):
    at = (now or datetime.now(TZ)).astimezone(TZ)
    return at.strftime('%Y-%m-%d'), at.strftime('%Y-%m'), int(at.timestamp())


def money_micro(value):
    amount = Decimal(str(value))
    if not amount.is_finite() or amount < 0 or amount > Decimal('1000000000'):
        raise ValueError('预算必须为有限非负金额')
    if amount * 1000000 != (amount * 1000000).to_integral_value():
        raise ValueError('金额最多六位小数')
    return int(amount * 1000000)


def _priced_tokens(price, usage):
    p = Price(**price)
    ordinary = usage.input_tokens - usage.cache_read_tokens - usage.cache_write_tokens
    if ordinary < 0 or usage.reasoning_tokens > usage.output_tokens or usage.cache_write_1h_tokens > usage.cache_write_tokens:
        raise ValueError('用量细分超过总量')
    if usage.cache_write_1h_tokens and p.cache_write_1h is None:
        raise ValueError('一小时缓存缺少定价')
    original = (ordinary*p.input + usage.output_tokens*p.output
                + usage.cache_read_tokens*p.cache_read
                + (usage.cache_write_tokens-usage.cache_write_1h_tokens)*p.cache_write
                + usage.cache_write_1h_tokens*(p.cache_write_1h or 0))
    return original,p.exchange_rate


def cost_micro(price, usage):
    original,rate=_priced_tokens(price,usage)
    # 单价为原币/百万 Token，乘百万微元后单位相消；始终向上取整。
    value=int((original*rate).to_integral_value(rounding=ROUND_CEILING))
    if value>10**15:raise ValueError('服务返回异常金额，需要人工核对')
    return value


async def set_limits(subject, *, day, month):
    if type(subject) is not int or subject < 0:
        raise ValueError('无效预算对象')
    values = [money_micro(v) if v is not None else None for v in (day, month)]
    db = await get_db()
    try:
        await db.execute('BEGIN IMMEDIATE')
        await _require_admin(db, require_user_id())
        if subject and not await (await db.execute('SELECT 1 FROM users WHERE id=?', (subject,))).fetchone():
            raise ValueError('用户不存在')
        await db.execute('INSERT INTO llm_budget_limits VALUES (?,?,?) ON CONFLICT(subject) DO UPDATE SET day=excluded.day,month=excluded.month', (subject, *values))
        from services import audit, accounts
        await audit.record(db, action='configuration_changed', outcome='success', created_at=accounts._now(None), actor_id=require_user_id())
        await db.commit()
    finally:
        await db.close()


async def _context_owner(db, context):
    uid = require_user_id()
    if context.purpose == 'diagnostic':
        if context.owner_id is not None:
            raise LLMError('invalid_context')
        await _require_admin(db, uid)
        return None
    if context.purpose != 'user' or context.owner_id != uid:
        raise LLMError('invalid_owner', '调用归属无效')
    row = await (await db.execute('SELECT * FROM users WHERE id=? AND active=1 AND must_change_password=0', (uid,))).fetchone()
    if not row:
        raise LLMError('inactive_user', '账号不可调用模型')
    return row


async def reserve_attempt(context, model_id, price_snapshot, max_cost, idempotency_key, *, now=None):
    if type(max_cost) is not int or max_cost < 0 or max_cost > 10**15:
        raise ValueError('预留金额无效')
    p = Price(**price_snapshot)
    day, month, timestamp = periods(now)
    db = await get_db()
    try:
        await db.execute('BEGIN IMMEDIATE')
        owner = await _context_owner(db, context)
        configured = await (await db.execute('SELECT config FROM llm_models WHERE id=?',(model_id,))).fetchone()
        service_id = json.loads(configured[0])['endpoint'].rstrip('/') if configured else model_id
        existing = await (await db.execute('SELECT * FROM llm_attempts WHERE idempotency_key=?', (idempotency_key,))).fetchone()
        if existing:
            if (existing['owner_id'],existing['purpose'],existing['task_id'],existing['model_id']) != (context.owner_id,context.purpose,context.task_id,model_id):
                raise LLMError('idempotency_conflict')
            return existing['id']
        if await (await db.execute('SELECT 1 FROM llm_service_holds WHERE model_id=?', (service_id,))).fetchone():
            raise LLMError('usage_unknown', '服务存在待核实费用，暂停新云端请求')
        subjects = [0] + ([context.owner_id] if owner else [])
        for subject in subjects:
            limits = await (await db.execute('SELECT * FROM llm_budget_limits WHERE subject=?', (subject,))).fetchone()
            for kind, period in [('day', day), ('month', month)]:
                limit = limits[kind] if limits else None
                if limit is None or limit <= 0:
                    raise LLMError('budget_unconfigured', '预算未配置或无云端额度')
                await db.execute('INSERT OR IGNORE INTO llm_budget_totals(subject,kind,period) VALUES (?,?,?)', (subject,kind,period))
                total = await (await db.execute('SELECT spent,reserved FROM llm_budget_totals WHERE subject=? AND kind=? AND period=?', (subject,kind,period))).fetchone()
                if total['spent']+total['reserved'] >= limit or total['spent']+total['reserved']+max_cost > limit:
                    raise LLMError('budget_exhausted', '云端预算不足')
        rid = uuid.uuid4().hex
        # uuid 在账号创建时生成，费用恢复匹配不能仅使用可复用整数 ID。
        user_uuid = owner['uuid'] if owner else None
        await db.execute('INSERT INTO llm_attempts(id,owner_id,user_uuid,purpose,task_id,scene,model_id,service_id,idempotency_key,day,month,price,reserved,state,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                         (rid,context.owner_id,user_uuid,context.purpose,context.task_id,context.scene,model_id,service_id,idempotency_key,day,month,p.model_dump_json(),max_cost,'reserved',timestamp))
        for subject in subjects:
            for kind,period in [('day',day),('month',month)]:
                await db.execute('UPDATE llm_budget_totals SET reserved=reserved+? WHERE subject=? AND kind=? AND period=?', (max_cost,subject,kind,period))
                await _alerts(db, subject,kind,period)
        await db.commit()
        return rid
    finally:
        await db.close()


async def _alerts(db, subject, kind, period):
    configured=await (await db.execute("SELECT value FROM app_config WHERE key='usage_alert_threshold'")).fetchone()
    warning=int(configured[0]) if configured else 80
    row = await (await db.execute(f'SELECT t.spent+t.reserved AS used,l.{kind} AS cap FROM llm_budget_totals t JOIN llm_budget_limits l ON l.subject=t.subject WHERE t.subject=? AND t.kind=? AND t.period=?', (subject,kind,period))).fetchone()
    if row and row['cap']:
        for threshold in (warning,100):
            if row['used']*100 >= row['cap']*threshold:
                await db.execute('INSERT OR IGNORE INTO llm_budget_alerts VALUES (?,?,?,?)', (subject,kind,period,threshold))
                from services.notifications import insert_event,insert_system
                key=f'{kind}:{period}:{threshold}'
                if subject==0:
                    await insert_system(db,'global_budget',key)
                else:
                    await insert_event(db,subject,'personal_budget',key,'个人预算提醒',f'本人{kind}预算达到 {threshold}% 阈值，请在用量页面查看。')


async def settle_attempt(reservation_id, usage_or_unknown, provider_request_id='', *, audit_reason=None):
    db = await get_db()
    try:
        await db.execute('BEGIN IMMEDIATE')
        row = await (await db.execute('SELECT * FROM llm_attempts WHERE id=?', (reservation_id,))).fetchone()
        if not row:
            raise LLMError('attempt_missing')
        if row['owner_id'] != require_user_id():
            await _require_admin(db, require_user_id())
        if audit_reason is not None:
            await _require_admin(db,require_user_id())
            if row['state'] != 'unknown':raise ValueError('只能核对待核实尝试')
        if row['state'] == 'settled':
            return
        if usage_or_unknown is None:
            await db.execute("UPDATE llm_attempts SET state='unknown' WHERE id=?", (reservation_id,))
            await db.execute('INSERT OR IGNORE INTO llm_service_holds VALUES (?)', (row['service_id'] or row['model_id'],))
        else:
            usage = usage_or_unknown if isinstance(usage_or_unknown, Usage) else Usage(**usage_or_unknown)
            price = json.loads(row['price'])
            cost = cost_micro(price, usage)
            original = str(_priced_tokens(price,usage)[0]/Decimal('1000000'))
            await db.execute("UPDATE llm_attempts SET state='settled',cost=?,original_cost=?,request_id=?,input_tokens=?,output_tokens=?,cache_read_tokens=?,cache_write_tokens=?,reasoning_tokens=?,cache_write_1h_tokens=? WHERE id=?",
                             (cost,original,provider_request_id[:200],usage.input_tokens,usage.output_tokens,usage.cache_read_tokens,usage.cache_write_tokens,usage.reasoning_tokens,usage.cache_write_1h_tokens,reservation_id))
            subjects = [0] + ([row['owner_id']] if row['owner_id'] is not None else [])
            for subject in subjects:
                for kind,period in [('day',row['day']),('month',row['month'])]:
                    await db.execute('UPDATE llm_budget_totals SET reserved=reserved-?,spent=spent+? WHERE subject=? AND kind=? AND period=?', (row['reserved'],cost,subject,kind,period))
                    await _alerts(db,subject,kind,period)
                await db.execute('INSERT INTO llm_usage_monthly VALUES (?,?,?,?,1,?,?) ON CONFLICT(subject,month,model_id,scene) DO UPDATE SET calls=calls+1,input_tokens=input_tokens+excluded.input_tokens,output_tokens=output_tokens+excluded.output_tokens',
                                 (subject,row['month'],row['model_id'],row['scene'],usage.input_tokens,usage.output_tokens))
            if cost > row['reserved']:
                await db.execute('INSERT OR IGNORE INTO llm_service_holds VALUES (?)', (row['service_id'] or row['model_id'],))
        await db.execute('INSERT INTO llm_usage_details VALUES (?,?,?) ON CONFLICT(attempt_id) DO UPDATE SET metadata=excluded.metadata',
                         (reservation_id,row['created_at'],json.dumps({'request_id':provider_request_id[:200]})))
        if audit_reason is not None:
            await db.execute('INSERT INTO llm_reconciliations(attempt_id,actor_id,reason,created_at) VALUES (?,?,?,?)',(reservation_id,require_user_id(),audit_reason,periods()[2]))
            await db.execute("DELETE FROM llm_service_holds WHERE model_id=? AND NOT EXISTS (SELECT 1 FROM llm_attempts WHERE COALESCE(service_id,model_id)=? AND state='unknown')",(row['service_id'] or row['model_id'],row['service_id'] or row['model_id']))
        await db.commit()
    finally:
        await db.close()


async def summary(*, now=None, subject=None):
    uid = require_user_id()
    db = await get_db()
    try:
        if subject is not None and subject != uid:
            await _require_admin(db,uid)
        target = uid if subject is None else subject
        day, month, _ = periods(now)
        result = {}
        account=await (await db.execute('SELECT role FROM users WHERE id=?',(uid,))).fetchone()
        for name, owner in [('user',target),('global',0)]:
            if name=='global' and (not account or account['role']!='admin'):
                continue
            limits = await (await db.execute('SELECT * FROM llm_budget_limits WHERE subject=?', (owner,))).fetchone()
            result[name] = {}
            for kind,period in [('day',day),('month',month)]:
                total = await (await db.execute('SELECT spent,reserved FROM llm_budget_totals WHERE subject=? AND kind=? AND period=?', (owner,kind,period))).fetchone()
                cap = limits[kind] if limits else None
                spent,reserved = (total['spent'],total['reserved']) if total else (0,0)
                result[name][kind] = {'period':period,'limit':cap,'spent':spent,'reserved':reserved,
                    'available':max(0,(cap or 0)-spent-reserved)}
        return result
    finally:
        await db.close()
