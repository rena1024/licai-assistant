"""页面 AI 分析按北京时间自然日复用；尝试状态和结果随账号持久保存。"""
import json
import uuid
from datetime import datetime, timedelta, timezone

from database import get_db
from services.task_context import ensure_active
from services.user_context import require_user_id


def today():
    return datetime.now(timezone(timedelta(hours=8))).date().isoformat()


_RETRY_MESSAGE = '今日自动分析未完成，请点击“重新生成”手动重试。'


async def run_daily(function, arguments, cache_key, day):
    owner = require_user_id()
    await ensure_active()
    db = await get_db()
    try:
        # 先登记当天的尝试，再进行任何付费操作；重启后也不会自动重跑。
        await db.execute('BEGIN IMMEDIATE')
        row = await (await db.execute(
            'SELECT value FROM user_settings WHERE user_id=? AND key=?', (owner, cache_key))).fetchone()
        previous = json.loads(row['value']) if row else {}
        same_day = previous.get('day') == day
        if same_day and not arguments.arguments.get('force'):
            return previous['result'] if previous.get('result') is not None else {'error': _RETRY_MESSAGE}
        entry = {'day': day, 'result': previous.get('result') if same_day else None,
                 'attempt': uuid.uuid4().hex}
        await db.execute(
            'INSERT INTO user_settings(user_id,key,value) VALUES (?,?,?) '
            'ON CONFLICT(user_id,key) DO UPDATE SET value=excluded.value',
            (owner, cache_key, json.dumps(entry, ensure_ascii=False)))
        await db.commit()
    finally:
        await db.close()

    # 每天首次访问必须取当天的新数据，不能命中旧的分钟级缓存。
    arguments.arguments['force'] = True
    result = await function(*arguments.args, **arguments.kwargs)
    await ensure_active()
    if not result.get('error'):
        entry['result'] = result
        db = await get_db()
        try:
            # 跨日或并行进程的新尝试优先，旧任务迟到不能覆盖新的结果。
            await db.execute(
                "UPDATE user_settings SET value=? WHERE user_id=? AND key=? "
                "AND json_extract(value,'$.attempt')=?",
                (json.dumps(entry, ensure_ascii=False), owner, cache_key, entry['attempt']))
            await db.commit()
        finally:
            await db.close()
    # 函数异常或业务失败时保留已登记的尝试及此前成功结果，等待手动重试。
    return result
