"""默认仅本机维护：每日备份、错过启动补一次及独立保留策略。"""
import asyncio
import time
from services import runtime_state,backup,audit,logging_config


async def maintenance_once(*,startup=False,now=None):
    now=int(time.time() if now is None else now)
    with runtime_state.runtime_scope():
        previous=runtime_state.read_state()
        from services.notifications import system_event,cleanup
        try:
            result=await asyncio.to_thread(backup.backup_if_due,now=now,startup=startup)
        except backup.BackupError:
            await audit.management_event('backup_failed')
            await system_event('backup_failed',f"backup:{previous.get('backup_success_at',0)}")
            logging_config.record_event(event='backup_failed',outcome='failure',elapsed_ms=0)
            return False
        if result and previous.get('backup_error'):
            await system_event('backup_recovered',f"backup:{previous.get('backup_error_at',0)}")
        if result:
            await audit.management_event('backup_created')
            logging_config.record_event(event='backup_created',outcome='success',elapsed_ms=0)
        from database import get_db
        db=await get_db()
        try:
            # 审计与运行日志独立保留；不按日志期限删除审计。
            days=runtime_state.read_state().get('audit_retention_days',180)
            if type(days) is not int or not 1<=days<=3650:days=180
            await db.execute('DELETE FROM audit_events WHERE created_at<?',(now-days*86400,))
            await db.commit()
        finally:await db.close()
        await cleanup(now=now)
        logging_config.cleanup_logs(now=now)
        return True


async def backup_loop():
    startup=True
    while True:
        try:
            await maintenance_once(startup=startup)
            startup=False
        except runtime_state.RuntimeUnavailable:
            pass
        except Exception:
            # 不把原始异常、文件路径或私人内容写入管理事件。
            logging_config.record_event(event='runtime_unavailable',outcome='failure',elapsed_ms=0)
        await asyncio.sleep(300)
