"""唯一外部渠道、不可变配置快照和可恢复的投递状态机。"""
from __future__ import annotations
import asyncio
import json
import re
import time
import uuid
from urllib.parse import urlsplit
from contextvars import ContextVar

_delivery_id=ContextVar("notification_delivery_id",default=None)


class DeliveryStopped(RuntimeError):
    pass
from database import get_db
from services.user_context import require_user_id,user_scope
from services import runtime_state


def validate_config(kind, config):
    if not isinstance(config,dict):
        raise ValueError('渠道配置无效')
    if kind=='pushplus':
        if set(config)!={'token'} or not isinstance(config['token'],str) or not 1<=len(config['token'])<=500:
            raise ValueError('请填写 PushPlus Token')
    elif kind=='feishu':
        if set(config)!={'webhook_url'}:
            raise ValueError('飞书配置无效')
        parsed=urlsplit(config['webhook_url'])
        if parsed.scheme!='https' or parsed.netloc!='open.feishu.cn' or not re.fullmatch(r'/open-apis/bot/v2/hook/[A-Za-z0-9-]+',parsed.path) or parsed.query or parsed.fragment:
            raise ValueError('只允许官方飞书 HTTPS Webhook')
    elif kind=='email':
        required={'host','port','security','username','password','from_address','to_address'}
        if set(config)!=required or config['security'] not in ('tls','starttls') or type(config['port']) is not int or config['port'] not in (465,587):
            raise ValueError('邮件必须使用 465 TLS 或 587 STARTTLS')
        if (config['security']=='tls') != (config['port']==465):
            raise ValueError('邮件端口与安全方式不匹配')
        if not isinstance(config['host'],str) or not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?',config['host']) or '.' not in config['host']:
            raise ValueError('SMTP 主机必须为公网域名')
        for field in ('username','password','from_address','to_address'):
            value=config[field]
            if not isinstance(value,str) or not value or len(value)>500 or '\r' in value or '\n' in value:
                raise ValueError('邮件字段无效')
        for field in ('from_address','to_address'):
            if not re.fullmatch(r'[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+',config[field]):
                raise ValueError('邮件地址无效')
    else:
        raise ValueError('不支持的通知渠道')
    return config


async def save_channel(data, *, expected_version):
    uid=require_user_id()
    if not isinstance(data,dict) or set(data)-{'id','kind','name','config'}:
        raise ValueError('渠道字段无效')
    identifier=data.get('id') or uuid.uuid4().hex
    name=data.get('name','').strip()
    if not name or len(name)>80:
        raise ValueError('请填写渠道名称（最多80字符）')
    db=await get_db()
    try:
        await db.execute('BEGIN IMMEDIATE')
        old=await (await db.execute('SELECT * FROM notification_channels WHERE id=? AND user_id=? AND deleted=0',(identifier,uid))).fetchone()
        if (old['version'] if old else 0)!=expected_version or (data.get('id') and not old):
            raise ValueError('渠道不存在或版本已变化')
        kind=data.get('kind')
        c=dict(data.get('config',{}))
        if old:
            if old['kind']!=kind:
                raise ValueError('修改渠道类型请新建配置')
            previous=json.loads((await (await db.execute('SELECT config FROM notification_channel_versions WHERE channel_id=? AND version=?',(identifier,old['version']))).fetchone())[0])
            for key in ('token','webhook_url','password'):
                if key in previous and not c.get(key):
                    c[key]=previous[key]
        validate_config(kind,c)
        await db.execute('INSERT INTO notification_channels VALUES (?,?,?,?,?,0) ON CONFLICT(id) DO UPDATE SET version=excluded.version,name=excluded.name',
                         (identifier,uid,expected_version+1,kind,name))
        await db.execute('INSERT INTO notification_channel_versions VALUES (?,?,?)',(identifier,expected_version+1,json.dumps(c)))
        await db.commit()
        return {'id':identifier,'version':expected_version+1}
    finally:
        await db.close()


async def channels():
    await migrate_legacy()
    uid=require_user_id()
    db=await get_db()
    try:
        rows=await (await db.execute('SELECT c.*,a.channel_id IS NOT NULL AS active,v.config FROM notification_channels c JOIN notification_channel_versions v ON v.channel_id=c.id AND v.version=c.version LEFT JOIN notification_active a ON a.channel_id=c.id AND a.user_id=c.user_id WHERE c.user_id=? AND c.deleted=0',(uid,))).fetchall()
        out=[]
        for row in rows:
            item=dict(row)
            config=json.loads(item.pop('config'))
            # 仅回显邮件非凭证字段；Webhook 内含凭证，不回传。
            item['fields']={k:v for k,v in config.items() if k not in ('token','password','webhook_url')}
            item['configured']=True
            item['credential_hint']='已保存（不回显）'
            out.append(item)
        return out
    finally:
        await db.close()


async def activate(identifier):
    uid=require_user_id()
    db=await get_db()
    try:
        await db.execute('BEGIN IMMEDIATE')
        if identifier is None:
            await db.execute('DELETE FROM notification_active WHERE user_id=?',(uid,))
            await db.execute("UPDATE notification_deliveries SET state='cancelled',error='用户关闭外部渠道' WHERE user_id=? AND state IN ('pending','retry')",(uid,))
        else:
            row=await (await db.execute('SELECT 1 FROM notification_channels WHERE id=? AND user_id=? AND deleted=0',(identifier,uid))).fetchone()
            if not row:
                raise ValueError('渠道不存在')
            await db.execute('INSERT INTO notification_active VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET channel_id=excluded.channel_id',(uid,identifier))
        await db.commit()
    finally:
        await db.close()


async def delete_channel(identifier):
    db=await get_db()
    try:
        await db.execute('BEGIN IMMEDIATE')
        uid=require_user_id()
        if await (await db.execute('SELECT 1 FROM notification_active WHERE user_id=? AND channel_id=?',(uid,identifier))).fetchone():
            raise ValueError('请先停用该渠道')
        await db.execute('UPDATE notification_channels SET deleted=1 WHERE id=? AND user_id=?',(identifier,uid))
        await db.execute("UPDATE notification_deliveries SET state='cancelled' WHERE channel_id=? AND user_id=? AND state IN ('pending','retry')",(identifier,uid))
        await db.commit()
    finally:
        await db.close()


async def send(kind,config,summary):
    from services.notification_adapters import pushplus,feishu,email
    from services.task_context import ensure_active_sync
    def invoke():
        ensure_active_sync()
        if runtime_state.read_state().get('notification_hold',False):
            raise DeliveryStopped('恢复后外发待确认')
        from config import config as runtime_config
        from services.runtime_paths import database_path
        import sqlite3
        with sqlite3.connect(database_path(runtime_config.db_path)) as db:
            current=db.execute("SELECT d.state,c.deleted,EXISTS(SELECT 1 FROM notification_active a WHERE a.user_id=d.user_id) FROM notification_deliveries d JOIN notification_channels c ON c.id=d.channel_id AND c.user_id=d.user_id WHERE d.id=? AND d.user_id=?",(_delivery_id.get(),require_user_id())).fetchone()
        if not current or current[0]!='sending' or current[1] or not current[2]:
            raise DeliveryStopped('用户已关闭外发或渠道已删除')
        return {'pushplus':pushplus,'feishu':feishu,'email':email}[kind].send(config,summary)
    return await asyncio.to_thread(invoke)


async def _claim(identifier,now):
    db=await get_db()
    try:
        await db.execute('BEGIN IMMEDIATE')
        if runtime_state.read_state().get('notification_hold',False):
            return None
        row=await (await db.execute('SELECT d.*,c.kind,c.deleted,v.config,n.event,u.active,u.auth_version AS current_auth FROM notification_deliveries d JOIN notification_channels c ON c.id=d.channel_id AND c.user_id=d.user_id JOIN notification_channel_versions v ON v.channel_id=d.channel_id AND v.version=d.channel_version JOIN notifications n ON n.id=d.notification_id AND n.user_id=d.user_id JOIN users u ON u.id=d.user_id WHERE d.id=?',(identifier,))).fetchone()
        if not row or row['state'] not in ('pending','retry') or row['next_at']>now:
            return None
        if not row['active'] or row['deleted'] or row['auth_version']!=row['current_auth']:
            await db.execute("UPDATE notification_deliveries SET state='cancelled',error='用户或渠道状态已变化' WHERE id=?",(identifier,))
            await db.commit()
            return None
        await db.execute("UPDATE notification_deliveries SET state='sending',attempts=attempts+1 WHERE id=?",(identifier,))
        await db.commit()
        return dict(row)
    finally:
        await db.close()


async def deliver_pending(*,now=None):
    now=int(time.time() if now is None else now)
    db=await get_db()
    try:
        identifiers=[r[0] for r in await (await db.execute("SELECT id FROM notification_deliveries WHERE state IN ('pending','retry') AND next_at<=? ORDER BY created_at LIMIT 30",(now,))).fetchall()]
    finally:
        await db.close()
    for identifier in identifiers:
        row=await _claim(identifier,now)
        if not row:
            continue
        # 固定摘要不包含正文、持仓数字、账号名或自定义标题。
        from services.notifications import SYSTEM
        summary=(SYSTEM[row['event']][0] if row['event'] in SYSTEM else '你有一条新的理财提醒')+'。请在应用内查看详情。局域网地址仅在同一网络或网络可达时可打开。'
        result='unknown'
        try:
            with user_scope(row['user_id']):
                from services.task_context import _version
                token=_version.set(row['auth_version'])
                delivery_token=_delivery_id.set(identifier)
                try:
                    result=await send(row['kind'],json.loads(row['config']),summary)
                finally:
                    _delivery_id.reset(delivery_token)
                    _version.reset(token)
            if result not in ('accepted','failed'):
                result='unknown'
        except DeliveryStopped:
            result="cancelled"
        except asyncio.CancelledError:
            raise  # 保留 sending，重启时转 unknown；不能盲目重发。
        except Exception as exc:
            from services.external_http import Rejected
            result='failed' if isinstance(exc,Rejected) else 'unknown'
        db=await get_db()
        try:
            attempt=row['attempts']+1
            state='retry' if result=='failed' and attempt<=2 else result
            next_at=now+(60 if attempt==1 else 300) if state=='retry' else now
            await db.execute("UPDATE notification_deliveries SET state=?,next_at=?,error=? WHERE id=? AND state='sending'",
                (state,next_at,'' if result=='accepted' else ('渠道明确失败' if result=='failed' else '发送前已停用' if result=='cancelled' else '结果未知，未自动重发'),identifier))
            await db.commit()
        finally:
            await db.close()


async def interrupt_pending():
    db=await get_db()
    try:
        await db.execute("UPDATE notification_deliveries SET state='unknown',error='发送期间服务中断，结果未知' WHERE state='sending'")
        await db.execute("UPDATE search_channels SET paused=1 WHERE provider IN (SELECT provider FROM search_attempts WHERE state='reserved')")
        await db.execute("UPDATE search_attempts SET state='unknown' WHERE state='reserved'")
        await db.commit()
    finally:
        await db.close()


async def resend(message_id, channel_id, *, confirmed):
    if confirmed is not True:
        raise ValueError('请确认重发目标；结果未知的消息可能重复')
    uid=require_user_id()
    db=await get_db()
    try:
        await db.execute('BEGIN IMMEDIATE')
        message=await (await db.execute('SELECT 1 FROM notifications WHERE id=? AND user_id=?',(message_id,uid))).fetchone()
        active=await (await db.execute('SELECT c.version,u.auth_version FROM notification_active a JOIN notification_channels c ON c.id=a.channel_id AND c.user_id=a.user_id JOIN users u ON u.id=a.user_id WHERE a.user_id=? AND a.channel_id=? AND c.deleted=0 AND u.active=1',(uid,channel_id))).fetchone()
        busy=await (await db.execute("SELECT 1 FROM notification_deliveries WHERE notification_id=? AND user_id=? AND state IN ('pending','retry','sending')",(message_id,uid))).fetchone()
        if not message or not active or busy:
            raise ValueError('消息、当前渠道已变化，或仍有投递在进行，请刷新')
        now=int(time.time())
        await db.execute('INSERT INTO notification_deliveries VALUES (?,?,?,?,?,?,?,0,?,?,?)',
            (uuid.uuid4().hex,message_id,uid,channel_id,active[0],active[1],'pending',now,now,''))
        await db.commit()
    finally:
        await db.close()


async def delivery_loop():
    while True:
        try:
            with runtime_state.runtime_scope():
                await deliver_pending()
        except asyncio.CancelledError:
            raise
        except Exception:
            from services.logging_config import record_event
            record_event(event='notification_delivery_failed',outcome='failure',elapsed_ms=0)
        await asyncio.sleep(15)


async def migrate_legacy():
    """旧个人飞书配置只迁一次；不覆盖已有活动渠道，恢复后不自动启用。"""
    from services import private_connections
    uid=require_user_id()
    url=private_connections.get('feishu_webhook','')
    if not url:
        return
    db=await get_db()
    migrated=False
    try:
        await db.execute('BEGIN IMMEDIATE')
        done=await (await db.execute("SELECT value FROM user_settings WHERE user_id=? AND key='notification_legacy_imported'",(uid,))).fetchone()
        if done:
            return
        try:
            config=validate_config('feishu',{'webhook_url':url})
        except ValueError:
            await db.execute("INSERT OR REPLACE INTO user_settings VALUES (?,'notification_legacy_imported','invalid')",(uid,))
            await db.commit()
            return
        existing=await (await db.execute('SELECT 1 FROM notification_channels WHERE user_id=?',(uid,))).fetchone()
        identifier=uuid.uuid4().hex
        await db.execute('INSERT INTO notification_channels VALUES (?,?,1,?,?,0)',(identifier,uid,'feishu','飞书（原配置）'))
        await db.execute('INSERT INTO notification_channel_versions VALUES (?,1,?)',(identifier,json.dumps(config)))
        if not existing and not private_connections.get('feishu_muted',False) and not runtime_state.read_state().get('last_restore_at'):
            await db.execute('INSERT OR IGNORE INTO notification_active VALUES (?,?)',(uid,identifier))
        await db.execute("INSERT OR REPLACE INTO user_settings VALUES (?,'notification_legacy_imported','done')",(uid,))
        await db.commit()
        migrated=True
    finally:
        await db.close()
    if migrated:
        private_connections.save('feishu_webhook','')
