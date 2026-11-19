"""消息归属、持久去重与外部投递分离；清理消息不清理事件去重。"""
import json
import time
import uuid
from database import get_db
from services.user_context import require_user_id

SCHEMA='''
CREATE TABLE IF NOT EXISTS notifications (
 id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, event TEXT NOT NULL, title TEXT NOT NULL,
 body TEXT NOT NULL, created_at INTEGER NOT NULL, read_at INTEGER
);
CREATE INDEX IF NOT EXISTS notifications_owner_time ON notifications(user_id,created_at);
CREATE TABLE IF NOT EXISTS notification_events (
 user_id INTEGER NOT NULL, event TEXT NOT NULL, event_key TEXT NOT NULL, notification_id TEXT NOT NULL,
 PRIMARY KEY(user_id,event,event_key)
);
CREATE TABLE IF NOT EXISTS notification_channels (
 id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, version INTEGER NOT NULL,
 kind TEXT NOT NULL, name TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS notification_channel_versions (
 channel_id TEXT NOT NULL, version INTEGER NOT NULL, config TEXT NOT NULL,
 PRIMARY KEY(channel_id,version)
);
CREATE TABLE IF NOT EXISTS notification_active (user_id INTEGER PRIMARY KEY, channel_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS notification_deliveries (
 id TEXT PRIMARY KEY, notification_id TEXT NOT NULL, user_id INTEGER NOT NULL,
 channel_id TEXT NOT NULL, channel_version INTEGER NOT NULL, auth_version INTEGER NOT NULL,
 state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, next_at INTEGER NOT NULL,
 created_at INTEGER NOT NULL, error TEXT NOT NULL DEFAULT ''
);
'''
PERSONAL={'personal_budget','briefing','summary','risk','model_switch','test','legacy'}
SYSTEM={
 'global_budget':('全局预算提醒','全局模型额度达到阈值，请在管理用量中查看。'),
 'backup_failed':('备份失败','自动备份未完成，请在运维页面检查。'),
 'backup_recovered':('备份恢复正常','自动备份已恢复正常。'),
 'source_failed':('公共数据源异常','部分公共数据源不可用，已按来源策略降级，请在数据中心查看。'),
 'source_recovered':('公共数据源恢复','公共数据源已恢复可用。'),
 'service_failed':('系统服务异常','系统服务出现异常，请在运维页面检查。')}


async def insert_event(db, uid, event, key, title, body, *, now=None, external=True):
    now=int(time.time() if now is None else now)
    existing=await (await db.execute('SELECT notification_id FROM notification_events WHERE user_id=? AND event=? AND event_key=?',(uid,event,key))).fetchone()
    if existing:
        return existing[0]
    user=await (await db.execute('SELECT active,auth_version FROM users WHERE id=?',(uid,))).fetchone()
    if not user or not user['active']:
        return None
    identifier=uuid.uuid4().hex
    await db.execute('INSERT INTO notifications VALUES (?,?,?,?,?,?,NULL)',(identifier,uid,event,title[:160],body[:20000],now))
    await db.execute('INSERT INTO notification_events VALUES (?,?,?,?)',(uid,event,key[:500],identifier))
    if external:
        active=await (await db.execute('SELECT c.id,c.version FROM notification_active a JOIN notification_channels c ON c.id=a.channel_id AND c.user_id=a.user_id WHERE a.user_id=? AND c.deleted=0',(uid,))).fetchone()
        if active:
            await db.execute('INSERT INTO notification_deliveries VALUES (?,?,?,?,?,?,?,0,?,?,?)',
                (uuid.uuid4().hex,identifier,uid,active['id'],active['version'],user['auth_version'],'pending',now,now,''))
    return identifier


async def emit(event, key, *, title, body='', external=True):
    if event not in PERSONAL:
        raise ValueError('无效个人事件')
    db=await get_db()
    try:
        await db.execute('BEGIN IMMEDIATE')
        identifier=await insert_event(db,require_user_id(),event,key,title,body,external=external)
        await db.commit()
        return identifier
    finally:
        await db.close()


async def insert_system(db,event,key,*,now=None):
    if event not in SYSTEM:
        raise ValueError('无效系统事件')
    admin=await (await db.execute("SELECT id FROM users WHERE role='admin' AND active=1")).fetchone()
    if admin:
        title,body=SYSTEM[event]
        return await insert_event(db,admin[0],event,key,title,body,now=now)


async def system_event(event,key,*,now=None):
    db=await get_db()
    try:
        await db.execute('BEGIN IMMEDIATE')
        result=await insert_system(db,event,key,now=now)
        await db.commit()
        return result
    finally:
        await db.close()


async def list_messages(*, limit=50, before=None, cursor=None):
    db=await get_db()
    try:
        uid=require_user_id()
        boundary=None
        if cursor:
            boundary=await (await db.execute('SELECT created_at,id FROM notifications WHERE id=? AND user_id=?',(cursor,uid))).fetchone()
            if not boundary:
                return []
        query='SELECT n.*, (SELECT state FROM notification_deliveries d WHERE d.notification_id=n.id ORDER BY d.created_at DESC,d.rowid DESC LIMIT 1) AS delivery_state FROM notifications n WHERE n.user_id=?'
        parameters=[uid]
        if before is not None:
            query+=' AND n.created_at<?'
            parameters.append(before)
        if boundary:
            query+=' AND (n.created_at<? OR (n.created_at=? AND n.id<?))'
            parameters.extend([boundary[0],boundary[0],boundary[1]])
        query+=' ORDER BY n.created_at DESC,n.id DESC LIMIT ?'
        parameters.append(min(max(limit,1),100))
        return [dict(r) for r in await (await db.execute(query,parameters)).fetchall()]
    finally:
        await db.close()


async def mark_read(identifier):
    db=await get_db()
    try:
        cursor=await db.execute('UPDATE notifications SET read_at=COALESCE(read_at,?) WHERE id=? AND user_id=?',(int(time.time()),identifier,require_user_id()))
        await db.commit()
        return cursor.rowcount>0
    finally:
        await db.close()


async def mark_many_read(identifiers):
    identifiers=list(dict.fromkeys(identifiers))
    if not 1<=len(identifiers)<=50:
        raise ValueError('每次须选择1～50条消息')
    db=await get_db()
    try:
        uid=require_user_id()
        placeholders=','.join('?' for _ in identifiers)
        await db.execute('BEGIN IMMEDIATE')
        count=await (await db.execute(
            f'SELECT COUNT(*) FROM notifications WHERE user_id=? AND id IN ({placeholders})',
            (uid,*identifiers),
        )).fetchone()
        # 先校验整批归属，避免混入无权访问的消息时只更新部分数据。
        if count[0]!=len(identifiers):
            await db.rollback()
            return False
        await db.execute(
            f'UPDATE notifications SET read_at=COALESCE(read_at,?) WHERE user_id=? AND id IN ({placeholders})',
            (int(time.time()),uid,*identifiers),
        )
        await db.commit()
        return True
    finally:
        await db.close()


async def delete_messages(identifiers):
    identifiers=list(dict.fromkeys(identifiers))
    if not 1<=len(identifiers)<=50:
        raise ValueError('每次须选择1～50条消息')
    db=await get_db()
    try:
        uid=require_user_id()
        placeholders=','.join('?' for _ in identifiers)
        parameters=(uid,*identifiers)
        await db.execute('BEGIN IMMEDIATE')
        count=await (await db.execute(
            f'SELECT COUNT(*) FROM notifications WHERE user_id=? AND id IN ({placeholders})',parameters,
        )).fetchone()
        if count[0]!=len(identifiers):
            await db.rollback()
            return False
        sending=await (await db.execute(
            f"SELECT 1 FROM notification_deliveries WHERE user_id=? AND notification_id IN ({placeholders}) AND state='sending' LIMIT 1",parameters,
        )).fetchone()
        if sending:
            raise ValueError('选中的通知正在发送，请稍后重试删除')
        # 同一事务清除投递队列，保留独立事件去重记录，避免重新生成或继续外发。
        await db.execute(f'DELETE FROM notification_deliveries WHERE user_id=? AND notification_id IN ({placeholders})',parameters)
        await db.execute(f'DELETE FROM notifications WHERE user_id=? AND id IN ({placeholders})',parameters)
        await db.commit()
        return True
    finally:
        await db.close()


async def cleanup(*,now=None):
    now=int(time.time() if now is None else now)
    db=await get_db()
    try:
        setting=await (await db.execute("SELECT value FROM app_config WHERE key='notification_retention_days'")).fetchone()
        days=int(setting[0]) if setting else 90
        cutoff=now-max(1,min(days,3650))*86400
        # 不清除仍待发送的消息；保留独立去重表。
        await db.execute("DELETE FROM notification_deliveries WHERE created_at<? AND state NOT IN ('pending','retry','sending')",(cutoff,))
        await db.execute('DELETE FROM notifications WHERE created_at<? AND id NOT IN (SELECT notification_id FROM notification_deliveries)',(cutoff,))
        await db.commit()
    finally:
        await db.close()


async def retention():
    from services.accounts import _require_admin
    db=await get_db()
    try:
        await _require_admin(db,require_user_id())
        row=await (await db.execute("SELECT value FROM app_config WHERE key='notification_retention_days'")).fetchone()
        return {'days':int(row[0]) if row else 90}
    finally:
        await db.close()


async def set_retention(days):
    from services.accounts import _require_admin
    if type(days) is not int or not 1<=days<=3650:
        raise ValueError('消息保留天数须为1～3650')
    db=await get_db()
    try:
        await _require_admin(db,require_user_id())
        await db.execute("INSERT INTO app_config VALUES ('notification_retention_days',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(days),))
        await db.commit()
        return {'days':days}
    finally:
        await db.close()


async def check_personal_risk():
    """仅在可核实时提醒本人，不用缺价/旧价生成完整风险结论。"""
    import math
    from datetime import datetime,timezone,timedelta
    from database import get_config
    from api.portfolio_routes import list_holdings
    rows=[h if isinstance(h,dict) else h.model_dump() for h in await list_holdings()]
    held=[h for h in rows if h.get('shares',0)>0]
    if not held or any(h.get('quote_quality') not in ('fresh','last_trade') or not h.get('current_price') for h in held):
        return
    opened=[h for h in held if h.get('market_state')=='open']
    if not opened:
        return
    pnl=0.0
    for h in opened:
        pct=h.get('price_change_pct')
        if pct is None or pct<=-100:
            return
        price=h['current_price']
        pnl+=(price-price/(1+pct/100))*h['shares']*(h.get('fx_rate') or 1)
    threshold=float(await get_config('max_daily_loss') or 500)
    if math.isfinite(threshold) and threshold>0 and pnl < -threshold:
        day=datetime.now(timezone(timedelta(hours=8))).date().isoformat()
        await emit('risk',f'daily_loss:{day}',title='个人日内风险提醒',
            body='当前交易中股票的日内浮动超过本人设置的风险阈值，请在持仓页面核对数据与覆盖范围。')
