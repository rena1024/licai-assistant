"""后台步骤以明确 owner 检查实时账号状态；退出浏览器不撤销任务。"""
from __future__ import annotations
import sqlite3
from contextvars import ContextVar
from services.user_context import require_user_id

_version: ContextVar[int | None] = ContextVar('task_auth_version', default=None)


def ensure_active_sync():
    from services import accounts
    from services.runtime_paths import database_path
    from config import config
    uid = require_user_id()
    with sqlite3.connect(database_path(config.db_path)) as db:
        row = db.execute('SELECT active,auth_version FROM users WHERE id=?', (uid,)).fetchone()
    if not row or not row[0] or (_version.get() is not None and _version.get() != row[1]):
        raise accounts.AccountError('forbidden')
    return row[1]


async def ensure_active():
    from database import get_db
    from services import accounts
    db = await get_db()
    try:
        row = await (await db.execute('SELECT active,auth_version FROM users WHERE id=?', (require_user_id(),))).fetchone()
        if not row or not row['active'] or (_version.get() is not None and _version.get() != row['auth_version']):
            raise accounts.AccountError('forbidden')
        return row['auth_version']
    finally:
        await db.close()
