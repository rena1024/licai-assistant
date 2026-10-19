"""个人任务分发：不依赖浏览器连接，逐步骤重查停用状态。"""
from __future__ import annotations
import asyncio
from datetime import datetime, timezone, timedelta
from database import get_db, get_user_setting, set_user_setting
from services.user_context import user_scope
from services.task_context import ensure_active, _version

_running: dict[int, set] = {}


def cancel_user(user_id: int):
    for task in list(_running.get(user_id, ())):
        task.cancel()


async def dispatch_personal(callback, *, ai: bool = False):
    db = await get_db()
    try:
        rows = await (await db.execute('SELECT id,auth_version FROM users WHERE active=1 AND must_change_password=0')).fetchall()
    finally:
        await db.close()
    for row in rows:
        uid = row['id']
        with user_scope(uid):
            token = _version.set(row['auth_version'])
            try:
                if ai and await get_user_setting('automatic_ai_enabled',user_id=uid) != '1':
                    continue
                await ensure_active()
                from services.llm_runtime import _background
                background_token = _background.set(True)
                try:
                    task = asyncio.create_task(callback())
                finally:
                    _background.reset(background_token)
                _running.setdefault(uid,set()).add(task)
                try:
                    await task
                except asyncio.CancelledError:
                    if asyncio.current_task().cancelling():
                        raise
                finally:
                    _running[uid].discard(task)
            except Exception:
                print(f'[scheduler] 用户 {uid} 后台任务失败')
            finally:
                _version.reset(token)


async def _once(name, day, callback):
    from services.user_context import require_user_id
    uid = require_user_id()
    key = f'task_done:{name}'
    if await get_user_setting(key,user_id=uid) == day:
        return
    await ensure_active()
    # 先持久占位，失败或重启不能重复触发通知／付费任务。
    await set_user_setting(key,day,user_id=uid)
    await callback()


async def personal_tick():
    from services.notification_delivery import migrate_legacy
    await migrate_legacy()
    from services.user_context import require_user_id
    from services.dca import fire_due_dcas
    from api.assets_routes import settle_pending_dca
    from services.portfolio_snapshot import maybe_snapshot
    from services.eod_summary import push_eod_summary
    from services.market_data import _is_a_share_trading_day
    now = datetime.now(timezone(timedelta(hours=8)))
    today = now.date().isoformat()
    await _once('dca', today, fire_due_dcas)
    await ensure_active()
    if now.hour >= 19:
        await _once('settle',today,settle_pending_dca)
    await ensure_active()
    await maybe_snapshot()
    from services.notifications import check_personal_risk
    await check_personal_risk()
    key = 'task_done:curve_window'
    window = str(int(now.timestamp()) // 1200)
    if await get_user_setting(key,user_id=require_user_id()) != window:
        await set_user_setting(key,window,user_id=require_user_id())
        from services.portfolio_curve import build_curve,correlation_matrix
        for days in (60,120,250):
            await ensure_active()
            await build_curve(days)
        await ensure_active()
        await correlation_matrix(60)
    minutes = now.hour * 60 + now.minute
    if 910 <= minutes <= 940 and _is_a_share_trading_day(now.date()):
        await _once('eod',today,push_eod_summary)
    if await get_user_setting('automatic_ai_enabled',user_id=require_user_id()) == '1':
        from services.morning_briefing import generate_all_briefings
        from services.rule_forge import mine_new
        if now.weekday() < 5 and 535 <= minutes <= 550:
            await _once('briefing',today,generate_all_briefings)
        if now.weekday() == 0 and 560 <= minutes <= 600:
            await _once('rules',today,mine_new)


async def personal_loop():
    while True:
        from services.runtime_state import runtime_scope,RuntimeUnavailable
        try:
            with runtime_scope():await dispatch_personal(personal_tick)
        except RuntimeUnavailable:pass
        await asyncio.sleep(60)


async def archive_public_close():
    from services.sector_share import archive_today
    from services.limit_up_pool import sync_today
    from services.stock_tags import sync_today as sync_tags
    await archive_today()
    await sync_today(public=True)
    await sync_tags()


async def public_loop():
    from services.market_data import _is_a_share_trading_day
    from database import get_config,set_config
    from services.runtime_state import runtime_scope,RuntimeUnavailable
    while True:
        try:
            # 整个 tick 共用代际，恢复后的下一轮重新获取代际。
            with runtime_scope():
                now = datetime.now(timezone(timedelta(hours=8)))
                from services.usage import purge_details
                await purge_details()
                day = now.date().isoformat()
                minutes = now.hour*60+now.minute
                if 910 <= minutes <= 940 and _is_a_share_trading_day(now.date()):
                    if await get_config('public_close_date') != day:
                        await set_config('public_close_date',day)
                        await archive_public_close()
                        from services.limit_up_pool import warm_pool_klines
                        from services.concept_trend import warm_cache
                        await warm_pool_klines()
                        await warm_cache()
        except RuntimeUnavailable:
            pass
        except Exception:
            print('[scheduler] 公共收盘归档失败')
        await asyncio.sleep(120)
