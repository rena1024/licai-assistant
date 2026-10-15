"""管理动作的结构化审计；不接收正文、凭证或任意异常对象。"""
from __future__ import annotations

import aiosqlite


ACTIONS = frozenset({"bootstrap_issued", "bootstrap_completed", "user_created", "password_recovered", "password_reset"})
OUTCOMES = frozenset({"success", "denied"})
REASONS = frozenset({"", "bootstrap_closed", "invalid_bootstrap", "expired_bootstrap", "bootstrap_missing",
                     "legacy_data", "forbidden", "invalid_username", "invalid_password", "username_taken",
                     "account_missing", "bootstrap_file"})


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
