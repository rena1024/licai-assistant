"""与模型预算独立的免费搜索；额度未知即关闭，预留在外部调用前提交。"""
from __future__ import annotations
import asyncio
import hashlib
import json
import time
import uuid
from datetime import datetime, timezone, timedelta
from pydantic import BaseModel, ConfigDict, Field
from database import get_db
from services.accounts import _require_admin
from services.user_context import require_user_id
from services.external_http import Rejected

SCHEMA = '''
CREATE TABLE IF NOT EXISTS search_channels (
 provider TEXT PRIMARY KEY, version INTEGER NOT NULL, config TEXT NOT NULL,
 paused INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS search_credits (
 account TEXT NOT NULL, period TEXT NOT NULL, used INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(account,period)
);
CREATE TABLE IF NOT EXISTS search_attempts (
 id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, account TEXT NOT NULL, period TEXT NOT NULL,
 provider TEXT NOT NULL, credits INTEGER NOT NULL, state TEXT NOT NULL, created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS search_preferences (user_id INTEGER PRIMARY KEY, enabled INTEGER NOT NULL);
'''
TZ = timezone(timedelta(hours=8))


class Channel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    provider: str = Field(pattern='^(tavily|exa)$')
    key: str = Field(default='', max_length=500)
    enabled: bool = False
    free_confirmed: bool = False
    auto_pay_disabled: bool = False
    exclusive_key: bool = False
    storage_allowed: bool = False
    remaining: int | None = Field(default=None, ge=0, le=1000000)
    credits_per_request: int = Field(default=1, ge=1, le=1000000)
    period: str = Field(pattern=r'^\d{4}-\d{2}$')
    verified_until: int = Field(default=0, ge=0)
    evidence: str = Field(default='', max_length=1000)


def account_id(channel):
    return hashlib.sha256((channel['provider']+':'+channel['key']).encode()).hexdigest()


async def save_channel(data, *, expected_version):
    c = Channel(**data).model_dump()
    db = await get_db()
    try:
        await db.execute('BEGIN IMMEDIATE')
        await _require_admin(db, require_user_id())
        old = await (await db.execute('SELECT * FROM search_channels WHERE provider=?',(c['provider'],))).fetchone()
        if (old['version'] if old else 0) != expected_version:
            raise ValueError('配置版本已变化，请刷新')
        if not c['key'] and old:
            c['key'] = json.loads(old['config'])['key']
        if c['enabled'] and not (c['key'] and c['remaining'] is not None and c['evidence'] and
                all(c[k] for k in ('free_confirmed','auto_pay_disabled','exclusive_key','storage_allowed'))):
            raise ValueError('须核实免费余额、无自动付费、独占凭证与引用存储条件')
        await db.execute('INSERT INTO search_channels VALUES (?,?,?,0) ON CONFLICT(provider) DO UPDATE SET version=excluded.version,config=excluded.config,paused=0',
                         (c['provider'], expected_version+1, json.dumps(c)))
        from services import audit, accounts
        await audit.record(db, action='configuration_changed',outcome='success',created_at=accounts._now(None),actor_id=require_user_id())
        await db.commit()
        return {'provider':c['provider'],'version':expected_version+1}
    finally:
        await db.close()


async def channels():
    db = await get_db()
    try:
        await _require_admin(db, require_user_id())
        out=[]
        for row in await (await db.execute('SELECT * FROM search_channels')).fetchall():
            c=json.loads(row['config'])
            account=account_id(c)
            spent=await (await db.execute('SELECT used FROM search_credits WHERE account=? AND period=?',(account,c['period']))).fetchone()
            c.update(key='',configured=bool(c['key']),version=row['version'],paused=bool(row['paused']),
                     available=None if c['remaining'] is None else max(0,c['remaining']-(spent[0] if spent else 0)))
            out.append(c)
        return out
    finally:
        await db.close()


async def preference(enabled=None):
    uid=require_user_id()
    db=await get_db()
    try:
        if enabled is not None:
            if type(enabled) is not bool:
                raise ValueError('搜索开关无效')
            await db.execute('INSERT INTO search_preferences VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET enabled=excluded.enabled',(uid,int(enabled)))
            await db.commit()
        row=await (await db.execute('SELECT enabled FROM search_preferences WHERE user_id=?',(uid,))).fetchone()
        return {'enabled':bool(row[0]) if row else True}
    finally:
        await db.close()


async def _reserve(provider, now):
    uid=require_user_id()
    db=await get_db()
    try:
        await db.execute('BEGIN IMMEDIATE')
        from services.runtime_state import check_generation
        check_generation()
        user=await (await db.execute('SELECT active,must_change_password FROM users WHERE id=?',(uid,))).fetchone()
        if not user or not user[0] or user[1]:
            return None
        pref=await (await db.execute('SELECT enabled FROM search_preferences WHERE user_id=?',(uid,))).fetchone()
        if pref and not pref[0]:
            return None
        row=await (await db.execute('SELECT * FROM search_channels WHERE provider=?',(provider,))).fetchone()
        if not row or row['paused']:
            return None
        c=json.loads(row['config'])
        period=datetime.fromtimestamp(now,TZ).strftime('%Y-%m')
        if not c['enabled'] or c['remaining'] is None or c['verified_until']<=now or c['period']!=period:
            return None
        minute=await (await db.execute('SELECT COUNT(*) FROM search_attempts WHERE user_id=? AND created_at>?',(uid,now-60))).fetchone()
        midnight=int(datetime.fromtimestamp(now,TZ).replace(hour=0,minute=0,second=0,microsecond=0).timestamp())
        daily=await (await db.execute('SELECT COUNT(*) FROM search_attempts WHERE user_id=? AND created_at>=?',(uid,midnight))).fetchone()
        if minute[0]>=10 or daily[0]>=100:
            return None
        account=account_id(c)
        await db.execute('INSERT OR IGNORE INTO search_credits VALUES (?,?,0)',(account,period))
        used=await (await db.execute('SELECT used FROM search_credits WHERE account=? AND period=?',(account,period))).fetchone()
        if used[0]+c['credits_per_request']>c['remaining']:
            return None
        attempt=uuid.uuid4().hex
        await db.execute('UPDATE search_credits SET used=used+? WHERE account=? AND period=?',(c['credits_per_request'],account,period))
        await db.execute('INSERT INTO search_attempts VALUES (?,?,?,?,?,?,?,?)',(attempt,uid,account,period,provider,c['credits_per_request'],'reserved',now))
        await db.commit()
        return attempt,c,row['version']
    finally:
        await db.close()


async def send(provider, key, query):
    from services.search_adapters import tavily, exa
    def invoke():
        ensure_search_active_sync()
        return (tavily if provider=='tavily' else exa).search(key, query)
    return await asyncio.to_thread(invoke)


async def search(query, *, now=None):
    now=int(time.time() if now is None else now)
    if not isinstance(query,str) or not query.strip() or len(query)>200:
        return {'status':'blocked','reason':'仅允许必要公开关键词，最多200字符','results':[]}
    query=await public_query(query)
    if not query:
        return {'status':'blocked','reason':'没有可发送的公开关键词','results':[]}
    if not (await preference())['enabled']:
        return {'status':'disabled','results':[]}
    # 每个供应商最多一次明确拒绝后的重试；每次都重新预留，未知结果由 paused 阻止重试。
    for provider in ('tavily','tavily','exa','exa'):
        from services.llm_runtime import _cancel
        if _cancel.get() is not None and _cancel.get().is_set():
            return {"status":"cancelled","reason":"任务已取消","results":[]}
        reservation=await _reserve(provider,now)
        if not reservation:
            continue
        attempt,c,version=reservation
        state='unknown'
        try:
            result=await send(provider,c['key'],query.strip())
            state='spent'
        except Rejected:
            state='rejected'
        except Exception:
            state='unknown'
        finally:
            db=await get_db()
            try:
                await db.execute('BEGIN IMMEDIATE')
                await db.execute('UPDATE search_attempts SET state=? WHERE id=?',(state,attempt))
                if state=='unknown':
                    await db.execute('UPDATE search_channels SET paused=1 WHERE provider=?',(provider,))
                await db.commit()
            finally:
                await db.close()
        if state=='spent':
            from services.task_context import ensure_active
            await ensure_active()
            return {'status':'ok','provider':provider,'results':result,'limitations':'仅获得链接和摘要，未读取全文；不可覆盖已验证行情'}
    return {'status':'unavailable','reason':'免费额度未知、未核验、暂停或已耗尽，未完成联网检索','results':[]}


async def public_query(raw):
    """只从公开证券字典和固定市场词提取关键词，绝不透传原问题。"""
    import re
    if not isinstance(raw,str) or len(raw)>2000:
        return ''
    # 先去除凭证、账户及金额片段，避免把六位账号误认成股票代码。
    cleaned = re.sub(r'(?i)(?:账户|账号|持仓|成本|数量|买入|卖出|token|password|secret|api[_ -]?key)\s*[:：=]?\s*[^，,;；\n]*', ' ', raw)
    symbols = re.findall(r'(?<![\w.])(?:[036]\d{5}|HK\.\d{5}|US\.[A-Z][A-Z.]{0,9})(?![\w.])',cleaned)
    topics = [word for word in ('新闻','公告','财报','政策','行业','产业链','宏观','利率','通胀','黄金','原油','半导体','人工智能','新能源','美联储','市场','经济','出口','销量','IPO') if word in cleaned]
    db=await get_db()
    try:
        names=await (await db.execute('SELECT name FROM symbol_dict WHERE length(name)>=2 AND instr(?,name)>0 LIMIT 5',(cleaned,))).fetchall()
    finally:
        await db.close()
    return ' '.join(dict.fromkeys(symbols+[row[0] for row in names]+topics))[:200]


async def web_search(raw):
    if not (await preference())['enabled']:
        return {'status':'disabled','error':'本人搜索已关闭','results':[]}
    query=await public_query(raw)
    if not query:
        return {'status':'blocked','error':'请使用公开证券代码、名称及市场关键词，不发送账户信息','results':[]}
    out=await search(query)
    if out['status']!='ok':
        out['error']=out.get('reason','未执行外部检索')
    return out


def ensure_search_active_sync():
    """每个物理搜索/正文请求前核对实时状态，重定向不能沿用入口许可。"""
    import sqlite3
    from services.task_context import ensure_active_sync
    from services.llm_runtime import _cancel
    from services.runtime_paths import database_path
    from config import config
    ensure_active_sync()
    if _cancel.get() is not None and _cancel.get().is_set():
        raise Rejected('任务已取消')
    with sqlite3.connect(database_path(config.db_path)) as db:
        row=db.execute('SELECT enabled FROM search_preferences WHERE user_id=?',(require_user_id(),)).fetchone()
    if row and not row[0]:
        raise Rejected('本人搜索已关闭')
