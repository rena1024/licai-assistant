"""公共来源按能力配置，状态仅含供应商元数据，不包含持仓和查询内容。"""
import json
import time
import sqlite3
from database import get_db
from services.accounts import _require_admin
from services.user_context import require_user_id

SCHEMA='''
CREATE TABLE IF NOT EXISTS data_source_policy (id INTEGER PRIMARY KEY CHECK(id=1),version INTEGER NOT NULL,config TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS data_source_status (
 capability TEXT NOT NULL,provider TEXT NOT NULL,market TEXT NOT NULL,
 last_attempt INTEGER NOT NULL,last_success INTEGER,available INTEGER NOT NULL,
 source_at TEXT,error TEXT NOT NULL,incident INTEGER NOT NULL,
 PRIMARY KEY(capability,provider,market)
);
CREATE TABLE IF NOT EXISTS kline_provenance (stock_code TEXT PRIMARY KEY,adjustment TEXT NOT NULL,provider TEXT NOT NULL);
'''
DEFAULT={'quotes':{m:['sina','tencent'] for m in ('A','HK','US')},
         'kline':['eastmoney','tencent'],'news':['em','cls','ths','jin10']}


async def policy():
    db=await get_db()
    try:
        try:
            row=await (await db.execute('SELECT version,config FROM data_source_policy WHERE id=1')).fetchone()
        except sqlite3.OperationalError as exc:
            if 'no such table' not in str(exc):
                raise
            row=None
        return json.loads(row['config']) if row else json.loads(json.dumps(DEFAULT))
    finally:
        await db.close()


async def save_policy(data,*,expected_version):
    if not isinstance(data,dict) or set(data)!=set(DEFAULT) or set(data['quotes'])!={'A','HK','US'}:
        raise ValueError('数据来源配置无效')
    for key,values in [(m,v) for m,v in data['quotes'].items()]+[('kline',data['kline']),('news',data['news'])]:
        allowed={'sina','tencent'} if key in ('A','HK','US') else ({'eastmoney','tencent'} if key=='kline' else {'em','cls','ths','jin10'})
        if not isinstance(values,list) or len(set(values))!=len(values) or any(v not in allowed for v in values):
            raise ValueError('供应商不支持该能力，或存在重复同源供应商')
    db=await get_db()
    try:
        await db.execute('BEGIN IMMEDIATE')
        await _require_admin(db,require_user_id())
        row=await (await db.execute('SELECT version FROM data_source_policy WHERE id=1')).fetchone()
        if (row[0] if row else 0)!=expected_version:
            raise ValueError('配置已变化，请刷新')
        await db.execute('INSERT INTO data_source_policy VALUES (1,?,?) ON CONFLICT(id) DO UPDATE SET version=excluded.version,config=excluded.config',(expected_version+1,json.dumps(data)))
        from services import audit,accounts
        await audit.record(db,action='configuration_changed',outcome='success',created_at=accounts._now(None),actor_id=require_user_id())
        await db.commit()
        from services import market_data
        market_data._cache.clear()
        from api import news_routes
        news_routes._cache.pop("market_news",None)
        return {'version':expected_version+1}
    finally:
        await db.close()


async def record(capability,provider,market,success,*,source_at=None,now=None):
    now=int(time.time() if now is None else now)
    db=await get_db()
    try:
        # 允许纯解析/报价合同测试在尚未初始化数据库时运行。
        if not await (await db.execute("SELECT 1 FROM sqlite_master WHERE name='data_source_status'")).fetchone():
            return
        await db.execute('BEGIN IMMEDIATE')
        row=await (await db.execute('SELECT * FROM data_source_status WHERE capability=? AND provider=? AND market=?',(capability,provider,market))).fetchone()
        incident=(row['incident'] if row else 0)+(1 if not success and (not row or row['available']) else 0)
        last_success=now if success else (row['last_success'] if row else None)
        await db.execute('INSERT INTO data_source_status VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(capability,provider,market) DO UPDATE SET last_attempt=excluded.last_attempt,last_success=excluded.last_success,available=excluded.available,source_at=excluded.source_at,error=excluded.error,incident=excluded.incident',
            (capability,provider,market,now,last_success,int(success),source_at if success else (row['source_at'] if row else None),'' if success else '供应商未返回有效数据',incident))
        from services.notifications import insert_system
        if not success and (not row or row['available']):
            await insert_system(db,'source_failed',f'{capability}:{provider}:{market}:{incident}',now=now)
        elif success and row and not row['available']:
            await insert_system(db,'source_recovered',f'{capability}:{provider}:{market}:{incident}',now=now)
        await db.commit()
    finally:
        await db.close()


async def overview():
    db=await get_db()
    try:
        await _require_admin(db,require_user_id())
        config=await (await db.execute('SELECT version,config FROM data_source_policy WHERE id=1')).fetchone()
        states=[dict(r) for r in await (await db.execute('SELECT * FROM data_source_status')).fetchall()]
        return {'version':config[0] if config else 0,'policy':json.loads(config[1]) if config else DEFAULT,
                'status':states,'coverage_note':'新浪与腾讯为独立供应商；A/H/美覆盖及实际时间单位需逐市场实测。K线A股前复权单独配置；港美股沿用既有独立历史路径。未实测不等于已验收。'}
    finally:
        await db.close()
