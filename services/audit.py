"""管理动作的结构化审计；不接收正文、凭证或任意异常对象。"""
from __future__ import annotations

import aiosqlite


ACTIONS = frozenset({"configuration_changed", "backup_created", "backup_failed", "restore_completed", "restore_failed", "trace_changed", "retention_completed", "user_enabled", "user_disabled", "user_deleted", "bootstrap_issued", "bootstrap_completed", "user_created", "password_recovered", "password_reset", "login", "logout", "password_changed"})
OUTCOMES = frozenset({"success", "denied"})
REASONS = frozenset({"", "bootstrap_closed", "invalid_bootstrap", "expired_bootstrap", "bootstrap_missing",
                     "legacy_data", "forbidden", "invalid_username", "invalid_password", "username_taken",
                     "account_missing", "user_active", "confirmation_mismatch", "user_busy", "bootstrap_file", "unauthenticated", "invalid_credentials", "password_unchanged"})


async def record(db: aiosqlite.Connection, *, action: str, outcome: str,
                 created_at: int, actor_id: int | None = None, target_id: int | None = None,
                 reason: str = "") -> None:
    if action not in ACTIONS or outcome not in OUTCOMES or reason not in REASONS:
        raise ValueError("审计仅接受已定义的动作、结果和原因代码")
    if any(value is not None and (type(value) is not int or value < 1) for value in (actor_id, target_id)):
        raise ValueError("审计对象必须是有效内部标识")
    await db.execute(
        "INSERT INTO audit_events (action, outcome, actor_id, target_id, reason, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (action, outcome, actor_id, target_id, reason, created_at),
    )


async def management_event(action,*,actor_id=None):
    import time
    from database import get_db
    db=await get_db()
    try:
        await record(db,action=action,outcome='success',created_at=int(time.time()),actor_id=actor_id)
        await db.commit()
    finally:await db.close()
