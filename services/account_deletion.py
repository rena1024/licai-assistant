"""删除个人数据，保留匿名费用身份；磁盘清理失败可持久重试。"""
import asyncio
import secrets
import shutil
import sys

from database import get_db
from services import accounts, audit, runtime_state
from services.runtime_paths import get_runtime_paths

SCHEMA = '''CREATE TABLE IF NOT EXISTS account_deletions (
 user_id INTEGER PRIMARY KEY REFERENCES users(id), deleted_at INTEGER NOT NULL,
 files_pending INTEGER NOT NULL DEFAULT 1
);'''

# 子表先于父表；费用尝试、用量汇总与审计不在清除清单内。
PERSONAL_TABLES = (
    'ask_message', 'ask_run', 'ask_session', 'external_asset_actions', 'external_assets',
    'position_actions', 'holdings', 'trade_log', 'alert_config', 'custom_alerts',
    'unwind_tranches', 'unwind_plans', 'morning_briefings', 'portfolio_snapshots',
    'watchlist_group', 'watchlist', 'cashflow_monthly', 'thesis_revision', 'position_thesis',
    'tool_gap', 'prompt_rule', 'brokers', 'user_settings', 'sessions', 'llm_preferences',
    'llm_local_jobs', 'notification_deliveries', 'notification_events', 'notifications',
    'notification_active', 'notification_channels', 'search_preferences',
)

# 已通过认证的旧请求也可能晚于删除事务返回；在写入边界阻止个人数据被重新创建。
WRITE_GUARDS = '\n'.join(
    f'''CREATE TRIGGER IF NOT EXISTS deleted_user_{table}_{operation.lower()}
    BEFORE {operation} ON {table}
    WHEN EXISTS (SELECT 1 FROM account_deletions WHERE user_id=NEW.user_id)
    BEGIN SELECT RAISE(ABORT, '账号已删除，禁止写入个人数据'); END;'''
    for table in PERSONAL_TABLES for operation in ('INSERT', 'UPDATE')
) + '\n' + '\n'.join(
    f'''CREATE TRIGGER IF NOT EXISTS deleted_user_{table}_{operation.lower()}
    BEFORE {operation} ON {table}
    WHEN NOT EXISTS (SELECT 1 FROM {parent} WHERE {key}=NEW.{foreign_key})
    BEGIN SELECT RAISE(ABORT, '账号已删除或关联数据不存在'); END;'''
    for table, parent, key, foreign_key in (
        ('dca_schedules', 'external_assets', 'id', 'asset_id'),
        ('notification_channel_versions', 'notification_channels', 'id', 'channel_id'),
    ) for operation in ('INSERT', 'UPDATE')
)


async def _target(db, actor_id, user_id, confirmation_username):
    await accounts._require_admin(db, actor_id)
    row = await (await db.execute('SELECT * FROM users WHERE id=? AND id NOT IN (SELECT user_id FROM account_deletions)', (user_id,))).fetchone()
    if not row:
        raise accounts.AccountError('account_missing')
    if row['role'] == 'admin':
        raise accounts.AccountError('forbidden')
    if row['active']:
        raise accounts.AccountError('user_active')
    if confirmation_username != row['username']:
        raise accounts.AccountError('confirmation_mismatch')
    return row


async def _stop_tasks(user_id):
    from services import ask_runs, scheduler, ai_jobs
    tasks = set(scheduler._running.get(user_id, ()))
    for run in list(ask_runs._RUNS.values()):
        if run.user_id == user_id and run.task and not run.task.done():
            run.cancel_event.set()
            tasks.add(run.task)
    tasks.update(ai_jobs.cancel_user(user_id))
    for task in tasks:
        task.cancel()
    if tasks:
        done, pending = await asyncio.wait(tasks, timeout=2)
        await asyncio.gather(*done, return_exceptions=True)
        if pending:
            raise accounts.AccountError('user_busy')


def _clear_memory(user_id):
    from services import ask_runs
    for identifier, run in list(ask_runs._RUNS.items()):
        if run.user_id == user_id:
            ask_runs._RUNS.pop(identifier, None)
    # 只访问已经加载的模块，避免为清缓存加载行情或第三方接入。
    caches = {
        'services.portfolio_risk': ('_cache',), 'services.today_pnl': ('_cache',),
        'services.event_calendar': ('_cache',), 'services.portfolio_curve': ('_cache',),
        'services.okx_client': ('_bot_details_cache',), 'services.providers.registry': ('_instances',),
        'api.news_routes': ('_cache',), 'api.market_routes': ('_senti_cache', '_senti_ai_cache'),
    }
    for module_name, names in caches.items():
        module = sys.modules.get(module_name)
        for name in names:
            cache = getattr(module, name, {})
            for key in list(cache):
                if key == user_id or (isinstance(key, tuple) and key and key[0] == user_id):
                    cache.pop(key, None)


async def _cleanup_files(actor_id, user_id):
    db = await get_db()
    try:
        await accounts._require_admin(db, actor_id)
        row = await (await db.execute('SELECT d.files_pending FROM account_deletions d JOIN users u ON u.id=d.user_id WHERE d.user_id=? AND u.active=0', (user_id,))).fetchone()
        if not row:
            raise accounts.AccountError('account_missing')
        if not row['files_pending']:
            return False
        paths = get_runtime_paths()
        try:
            for directory in (paths.config / 'users' / str(user_id), paths.media / str(user_id)):
                # 不解析后再删除：运行目录内部的软链接也可能指向另一个账号。
                for part in (directory, *directory.parents):
                    if part == paths.root:
                        break
                    if part.is_symlink():
                        raise OSError('不允许通过软链接清理个人目录')
                paths.validate(directory)
                if directory.exists():
                    shutil.rmtree(directory)
        except OSError:
            return True
        await db.execute('UPDATE account_deletions SET files_pending=0 WHERE user_id=?', (user_id,))
        await db.commit()
        return False
    finally:
        await db.close()


async def _delete_data(actor_id, user_id, confirmation_username):
    async with accounts._transaction('user_deleted', now=None, actor_id=actor_id, target_id=user_id) as db:
        row = await _target(db, actor_id, user_id, confirmation_username)
        for query in (
            "SELECT 1 FROM llm_attempts WHERE owner_id=? AND state IN ('reserved','local_pending')",
            "SELECT 1 FROM search_attempts WHERE user_id=? AND state='reserved'",
            "SELECT 1 FROM notification_deliveries WHERE user_id=? AND state='sending'",
            "SELECT 1 FROM llm_local_jobs WHERE user_id=? AND status IN ('queued','running')",
        ):
            if await (await db.execute(query, (user_id,))).fetchone():
                raise accounts.AccountError('user_busy')
        from services.llm_activity import user_has_calls
        if user_has_calls(user_id):
            raise accounts.AccountError('user_busy')
        await db.execute('DELETE FROM dca_schedules WHERE asset_id IN (SELECT id FROM external_assets WHERE user_id=?)', (user_id,))
        await db.execute('DELETE FROM notification_channel_versions WHERE channel_id IN (SELECT id FROM notification_channels WHERE user_id=?)', (user_id,))
        for table in PERSONAL_TABLES:
            await db.execute(f'DELETE FROM {table} WHERE user_id=?', (user_id,))
        for table in ('llm_budget_limits', 'llm_budget_alerts'):
            await db.execute(f'DELETE FROM {table} WHERE subject=?', (user_id,))
        timestamp = accounts._now(None)
        # 身份行只供历史费用按 UUID 关联，不保留原用户名、密码和个人设置。
        password_hash = accounts._password_hash.hash(secrets.token_urlsafe(48))
        await db.execute('UPDATE users SET username=?,password_hash=?,active=0,must_change_password=0,auth_version=auth_version+1,password_reset_at=NULL,created_at=? WHERE id=?',
                         ('!deleted:' + row['uuid'], password_hash, timestamp, user_id))
        await db.execute('INSERT INTO account_deletions VALUES (?,?,1)', (user_id, timestamp))
        await audit.record(db, action='user_deleted', outcome='success', created_at=timestamp, actor_id=actor_id, target_id=user_id)
    _clear_memory(user_id)
    try:
        pending = await _cleanup_files(actor_id, user_id)
    except accounts.AccountError:
        # 提交后管理员状态若变化，数据删除已经完成；保留待清理标记供下次重试。
        pending = True
    return {'deleted': True, 'files_pending': pending}


def _locked_run(function, *args):
    # 在独立线程持锁执行，避免主事件循环等待数据库时持有可重入文件锁。
    key, _ = runtime_state.acquire_connection()
    try:
        with runtime_state.file_lock:
            runtime_state.check_generation()
            return asyncio.run(function(*args))
    finally:
        runtime_state.release_connection(key)


async def delete_user(*, actor_id, user_id, confirmation_username):
    async with accounts._transaction('user_deleted', now=None, actor_id=actor_id, target_id=user_id) as db:
        await _target(db, actor_id, user_id, confirmation_username)
    await _stop_tasks(user_id)
    result = await asyncio.to_thread(_locked_run, _delete_data, actor_id, user_id, confirmation_username)
    return result


async def pending_cleanups(*, actor_id):
    db = await get_db()
    try:
        await accounts._require_admin(db, actor_id)
        return [{'id': row[0]} for row in await (await db.execute('SELECT d.user_id FROM account_deletions d JOIN users u ON u.id=d.user_id WHERE d.files_pending=1 AND u.active=0 ORDER BY d.user_id')).fetchall()]
    finally:
        await db.close()


async def retry_cleanup(*, actor_id, user_id):
    pending = await asyncio.to_thread(_locked_run, _cleanup_files, actor_id, user_id)
    return {'deleted': True, 'files_pending': pending}


async def retry_pending_cleanups():
    db = await get_db()
    try:
        admin = await (await db.execute("SELECT id FROM users WHERE role='admin' AND active=1 AND must_change_password=0")).fetchone()
    finally:
        await db.close()
    if admin:
        for item in await pending_cleanups(actor_id=admin[0]):
            await retry_cleanup(actor_id=admin[0], user_id=item['id'])
