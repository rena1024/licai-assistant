"""账号基础能力。调用者身份仅供服务端内部使用，不接受网页自报管理员 ID。"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
import hashlib
import hmac
import logging
import os
from pathlib import Path
import secrets
import sqlite3
import tempfile
import time
import unicodedata
import uuid

from pwdlib import PasswordHash

from database import get_db, DEFAULT_BROKERS
from services import audit
from services.runtime_paths import get_runtime_paths


logger = logging.getLogger(__name__)
_password_hash = PasswordHash.recommended()
BOOTSTRAP_TTL = 24 * 60 * 60
_ERRORS = {
    "bootstrap_closed": (409, "初始化已经完成，不能再次初始化"),
    "invalid_bootstrap": (403, "初始化凭据无效"),
    "expired_bootstrap": (403, "初始化凭据已过期，请在本机重新生成"),
    "bootstrap_missing": (409, "请先在部署机器生成初始化凭据"),
    "bootstrap_file": (409, "初始化凭据文件缺失或不匹配，请在本机重新生成"),
    "legacy_data": (409, "发现无归属的历史业务数据，请使用新的空运行目录"),
    "forbidden": (403, "当前账号无权执行此操作"),
    "invalid_username": (422, "账号名须为 3～64 位字母、数字、点、下划线或连字符，且以字母或数字开头"),
    "invalid_password": (422, "密码长度须为 4～128 个字符"),
    "username_taken": (409, "账号名已存在"),
    "account_missing": (404, "账号不存在"),
}

# 公开行情缓存不属于初始化拦截范围；自动生成的券商模板也不是用户业务。
_PRIVATE_TABLES = (
    "holdings", "trade_log", "alert_config", "custom_alerts", "unwind_plans", "unwind_tranches",
    "external_assets", "position_actions", "morning_briefings", "portfolio_snapshots", "watchlist",
    "watchlist_group", "cashflow_monthly", "external_asset_actions", "dca_schedules", "position_thesis",
    "thesis_revision", "ask_session", "ask_message", "ask_run", "tool_gap", "prompt_rule",
)


class AccountError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        self.status_code, message = _ERRORS[code]
        super().__init__(message)


@dataclass(frozen=True)
class Account:
    id: int
    uuid: str
    username: str
    role: str
    active: bool
    must_change_password: bool
    auth_version: int
    password_reset_at: int | None


def _account(row) -> Account:
    return Account(**{key: bool(row[key]) if key in {"active", "must_change_password"} else row[key]
                      for key in Account.__dataclass_fields__})


def _now(now: int | None) -> int:
    return int(time.time()) if now is None else now


def _username(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).strip().casefold()
    if not 3 <= len(value) <= 64 or not value[0].isalnum() or not all(c.isalnum() or c in "_.-" for c in value):
        raise AccountError("invalid_username")
    return value


def _validate_password(password: str) -> None:
    # 不裁剪密码，空格也是密码的一部分。
    if not 4 <= len(password) <= 128:
        raise AccountError("invalid_password")


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@asynccontextmanager
async def _transaction(action: str, *, now: int | None, actor_id: int | None = None, target_id: int | None = None):
    db = await get_db()
    try:
        # SQLite 事务同时保护凭据消费、唯一管理员、会话撤销和成功审计。
        await db.execute("BEGIN IMMEDIATE")
        yield db
        await db.commit()
    except AccountError as exc:
        await db.rollback()
        await audit.record(db, action=action, outcome="denied", created_at=_now(now),
                           actor_id=actor_id, target_id=target_id, reason=exc.code)
        await db.commit()
        raise
    except BaseException:
        await db.rollback()
        raise
    finally:
        await db.close()


async def _has_users(db) -> bool:
    return await (await db.execute("SELECT 1 FROM users LIMIT 1")).fetchone() is not None


async def _assert_fresh_business(db) -> None:
    for table in _PRIVATE_TABLES:
        # 表名来自常量白名单，不接受外部输入。
        if await (await db.execute(f'SELECT 1 FROM "{table}" LIMIT 1')).fetchone():
            raise AccountError("legacy_data")
    # 空库会生成迁移标记，A01 启动也可能自动保存公共代理；两者均无个人归属。
    # 这里只放行明确的系统键，其他配置仍按可能含私人凭证处理。
    config_row = await (await db.execute(
        "SELECT 1 FROM app_config WHERE NOT ((key='watchlist_default_group_filled' AND value='1') "
        "OR key='network_proxy') LIMIT 1"
    )).fetchone()
    if config_row:
        raise AccountError("legacy_data")
    brokers = await (await db.execute("SELECT name, stock_rate, stock_min, etf_rate, etf_min, is_default FROM brokers")).fetchall()
    if {tuple(row) for row in brokers} != set(DEFAULT_BROKERS):
        raise AccountError("legacy_data")


def _publish_token(path: Path, token: str) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(token + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        # NamedTemporaryFile 使用独占创建，POSIX 下权限为 0600；替换不沿目标软链接写入。
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


async def issue_bootstrap_token(*, renew: bool = False, now: int | None = None) -> Path | None:
    pending_file = None
    old_file = None
    try:
        async with _transaction("bootstrap_issued", now=now) as db:
            if await _has_users(db):
                return None
            await _assert_fresh_business(db)
            timestamp = _now(now)
            state = await (await db.execute("SELECT * FROM bootstrap_state WHERE id=1")).fetchone()
            if state:
                old_file = get_runtime_paths().file("config", state["token_file"])
            if state and not renew:
                if state["expires_at"] <= timestamp:
                    raise AccountError("expired_bootstrap")
                try:
                    raw = old_file.read_text(encoding="utf-8").strip()
                except FileNotFoundError:
                    raise AccountError("bootstrap_file") from None
                if not hmac.compare_digest(_token_hash(raw), state["token_hash"]):
                    raise AccountError("bootstrap_file")
                return old_file
            token = secrets.token_urlsafe(32)
            # 每代凭据使用不可变文件，提交失败也不覆盖仍然有效的上一代。
            pending_file = get_runtime_paths().file("config", f"bootstrap-token-{secrets.token_hex(16)}")
            _publish_token(pending_file, token)
            await db.execute(
                "INSERT INTO bootstrap_state VALUES (1, ?, ?, ?, ?, NULL) "
                "ON CONFLICT(id) DO UPDATE SET token_hash=excluded.token_hash, token_file=excluded.token_file, "
                "created_at=excluded.created_at, expires_at=excluded.expires_at, consumed_at=NULL",
                (_token_hash(token), pending_file.name, timestamp, timestamp + BOOTSTRAP_TTL),
            )
            await audit.record(db, action="bootstrap_issued", outcome="success", created_at=timestamp)
    except BaseException:
        if pending_file is not None:
            await _discard_unreferenced_token(pending_file)
        raise
    if old_file is not None:
        _remove_token_file(old_file)
    return pending_file


def _remove_token_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("本机初始化凭据文件未清理，请在部署机器检查")


async def _discard_unreferenced_token(path: Path) -> None:
    # 提交被取消时可能已在 SQLite 线程完成；先查持久状态，不能误删已生效的一代。
    try:
        db = await get_db()
        try:
            state = await (await db.execute("SELECT token_file FROM bootstrap_state WHERE id=1")).fetchone()
            if not state or state["token_file"] != path.name:
                _remove_token_file(path)
        finally:
            await db.close()
    except (OSError, sqlite3.Error):
        logger.warning("初始化凭据文件状态待本机检查；未删除可能有效的凭据")


async def bootstrap_status(*, now: int | None = None) -> dict:
    db = await get_db()
    try:
        initialized = await _has_users(db)
        row = await (await db.execute("SELECT expires_at, consumed_at FROM bootstrap_state WHERE id=1")).fetchone()
        return {"initialized": initialized, "credential_available": bool(
            not initialized and row and row["consumed_at"] is None and row["expires_at"] > _now(now)
        )}
    finally:
        await db.close()


async def _insert_user(db, *, username: str, password: str, role: str, timestamp: int) -> Account:
    encoded = await asyncio.to_thread(_password_hash.hash, password)
    try:
        cursor = await db.execute(
            "INSERT INTO users (uuid, username, password_hash, role, must_change_password, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), username, encoded, role, int(role == "user"), timestamp),
        )
    except sqlite3.IntegrityError:
        raise AccountError("username_taken") from None
    row = await (await db.execute("SELECT * FROM users WHERE id=?", (cursor.lastrowid,))).fetchone()
    return _account(row)


async def bootstrap_admin(*, token: str, username: str, password: str, now: int | None = None) -> Account:
    name = _username(username)
    _validate_password(password)
    async with _transaction("bootstrap_completed", now=now) as db:
        if await _has_users(db):
            raise AccountError("bootstrap_closed")
        await _assert_fresh_business(db)
        state = await (await db.execute("SELECT * FROM bootstrap_state WHERE id=1")).fetchone()
        timestamp = _now(now)
        if not state:
            raise AccountError("bootstrap_missing")
        if state["consumed_at"] is not None or len(token) != 43 or not hmac.compare_digest(_token_hash(token), state["token_hash"]):
            raise AccountError("invalid_bootstrap")
        if state["expires_at"] <= timestamp:
            raise AccountError("expired_bootstrap")
        admin = await _insert_user(db, username=name, password=password, role="admin", timestamp=timestamp)
        await db.execute("UPDATE bootstrap_state SET consumed_at=? WHERE id=1", (timestamp,))
        await audit.record(db, action="bootstrap_completed", outcome="success", created_at=timestamp, target_id=admin.id)
    # 数据库消费是有效性依据；文件删除失败不能撤回已经完成的管理员初始化。
    _remove_token_file(get_runtime_paths().file("config", state["token_file"]))
    return admin


async def _require_admin(db, actor_id: int) -> None:
    row = await (await db.execute("SELECT role, active, must_change_password FROM users WHERE id=?", (actor_id,))).fetchone()
    if not row or row["role"] != "admin" or not row["active"] or row["must_change_password"]:
        raise AccountError("forbidden")


async def create_user(*, actor_id: int, username: str, password: str, now: int | None = None) -> Account:
    name = _username(username)
    _validate_password(password)
    async with _transaction("user_created", now=now, actor_id=actor_id) as db:
        await _require_admin(db, actor_id)
        timestamp = _now(now)
        user = await _insert_user(db, username=name, password=password, role="user", timestamp=timestamp)
        await audit.record(db, action="user_created", outcome="success", created_at=timestamp,
                           actor_id=actor_id, target_id=user.id)
        return user


async def _replace_password(db, user_id: int, password: str, timestamp: int) -> Account:
    encoded = await asyncio.to_thread(_password_hash.hash, password)
    await db.execute(
        "UPDATE users SET password_hash=?, must_change_password=1, auth_version=auth_version+1, password_reset_at=? WHERE id=?",
        (encoded, timestamp, user_id),
    )
    await db.execute("UPDATE sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL", (timestamp, user_id))
    return _account(await (await db.execute("SELECT * FROM users WHERE id=?", (user_id,))).fetchone())


async def reset_user_password(*, actor_id: int, user_id: int, password: str, now: int | None = None) -> Account:
    _validate_password(password)
    async with _transaction("password_reset", now=now, actor_id=actor_id, target_id=user_id) as db:
        await _require_admin(db, actor_id)
        target = await (await db.execute("SELECT role FROM users WHERE id=?", (user_id,))).fetchone()
        if not target:
            raise AccountError("account_missing")
        if target["role"] == "admin":
            raise AccountError("forbidden")
        timestamp = _now(now)
        user = await _replace_password(db, user_id, password, timestamp)
        await audit.record(db, action="password_reset", outcome="success", created_at=timestamp,
                           actor_id=actor_id, target_id=user_id)
        return user


async def recover_admin_password(*, password: str, now: int | None = None) -> Account:
    """仅本机 CLI 调用，不注册任何匿名网页恢复路由。"""
    _validate_password(password)
    async with _transaction("password_recovered", now=now) as db:
        row = await (await db.execute("SELECT id FROM users WHERE role='admin'")).fetchone()
        if not row:
            raise AccountError("account_missing")
        timestamp = _now(now)
        user = await _replace_password(db, row["id"], password, timestamp)
        await audit.record(db, action="password_recovered", outcome="success", created_at=timestamp, target_id=user.id)
        return user
