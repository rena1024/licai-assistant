"""服务端不透明会话。原始凭据只放在 HttpOnly Cookie，数据库仅保存摘要。"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import hmac
import secrets

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from database import get_db
from services import accounts, audit
from services.user_context import user_scope

COOKIE_NAME = "licai_session"
SESSION_TTL = 30 * 86400
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class AuthError(accounts.AccountError):
    def __init__(self, code="unauthenticated"):
        self.code = code
        self.status_code, message = {
            "unauthenticated": (401, "登录已失效，请重新登录"),
            "invalid_credentials": (401, "账号或密码不正确"),
            "password_unchanged": (422, "新密码不能与当前密码相同"),
        }[code]
        RuntimeError.__init__(self, message)


@dataclass(frozen=True)
class UserContext:
    user_id: int
    session_id: str
    auth_version: int


@dataclass(frozen=True)
class Session:
    account: accounts.Account
    context: UserContext
    expires_at: int
    token: str = ""


def csrf_token(token: str) -> str:
    return hashlib.sha256(("licai-csrf:" + token).encode()).hexdigest()


# 未知账号也执行 Argon2 校验，避免通过是否计算密码区分账号是否存在。
_dummy_hash = accounts._password_hash.hash(secrets.token_urlsafe(32))


async def _verify(password: str, encoded: str) -> bool:
    return await asyncio.to_thread(accounts._password_hash.verify, password, encoded)


async def login(*, username: str, password: str, replace_token: str = "", now: int | None = None) -> Session:
    try:
        name = accounts._username(username)
    except accounts.AccountError:
        name = ""
    async with accounts._transaction("login", now=now) as db:
        row = await (await db.execute("SELECT * FROM users WHERE username=?", (name,))).fetchone()
        valid = await _verify(password, row["password_hash"] if row else _dummy_hash)
        if not valid or not row or not row["active"]:
            raise AuthError("invalid_credentials")
        token = secrets.token_urlsafe(32)
        sid = secrets.token_hex(16)
        timestamp = accounts._now(now)
        expires = timestamp + SESSION_TTL
        # 新账号校验成功后才替换当前浏览器的旧会话；失败或取消不影响原登录。
        if replace_token:
            previous = await (await db.execute(
                "SELECT user_id FROM sessions WHERE token_hash=? AND revoked_at IS NULL",
                (accounts._token_hash(replace_token),),
            )).fetchone()
            if previous:
                await db.execute("UPDATE sessions SET revoked_at=? WHERE token_hash=?", (timestamp, accounts._token_hash(replace_token)))
                await audit.record(db, action="logout", outcome="success", created_at=timestamp, actor_id=previous["user_id"])
        await db.execute(
            "INSERT INTO sessions (session_id, token_hash, user_id, auth_version, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
            (sid, accounts._token_hash(token), row["id"], row["auth_version"], timestamp, expires),
        )
        await audit.record(db, action="login", outcome="success", created_at=timestamp, actor_id=row["id"])
        return Session(accounts._account(row), UserContext(row["id"], sid, row["auth_version"]), expires, token)


async def _lookup(db, token: str, now: int | None):
    if len(token) != 43:
        raise AuthError()
    row = await (await db.execute(
        "SELECT u.*, s.session_id, s.expires_at FROM sessions s JOIN users u ON u.id=s.user_id "
        "WHERE s.token_hash=? AND s.revoked_at IS NULL AND s.expires_at>? "
        "AND s.auth_version=u.auth_version AND u.active=1",
        (accounts._token_hash(token), accounts._now(now)),
    )).fetchone()
    if not row:
        raise AuthError()
    return row


async def authenticate(token: str, *, now: int | None = None) -> Session:
    db = await get_db()
    try:
        row = await _lookup(db, token, now)
        return Session(accounts._account(row), UserContext(row["id"], row["session_id"], row["auth_version"]), row["expires_at"])
    finally:
        await db.close()


async def logout(token: str, *, now: int | None = None) -> None:
    async with accounts._transaction("logout", now=now) as db:
        row = await _lookup(db, token, now)
        timestamp = accounts._now(now)
        await db.execute("UPDATE sessions SET revoked_at=? WHERE session_id=?", (timestamp, row["session_id"]))
        await audit.record(db, action="logout", outcome="success", created_at=timestamp, actor_id=row["id"])


async def change_password(token: str, *, current_password: str, new_password: str, now: int | None = None) -> None:
    accounts._validate_password(new_password)
    async with accounts._transaction("password_changed", now=now) as db:
        row = await _lookup(db, token, now)
        if not await _verify(current_password, row["password_hash"]):
            raise AuthError("invalid_credentials")
        if hmac.compare_digest(current_password.encode(), new_password.encode()):
            raise AuthError("password_unchanged")
        timestamp = accounts._now(now)
        await accounts._replace_password(db, row["id"], new_password, timestamp)
        await db.execute("UPDATE users SET must_change_password=0 WHERE id=?", (row["id"],))
        await audit.record(db, action="password_changed", outcome="success", created_at=timestamp, actor_id=row["id"])


def check_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin is not None and origin != f"{request.url.scheme}://{request.url.netloc}":
        raise HTTPException(403, "请求来源不被允许")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "请求来源不被允许")


async def require_session(request: Request, *, csrf: bool = False) -> Session:
    token = request.cookies.get(COOKIE_NAME, "")
    session = await authenticate(token)
    if csrf:
        check_origin(request)
        if not hmac.compare_digest(request.headers.get("x-csrf-token", "").encode(), csrf_token(token).encode()):
            raise HTTPException(403, "请求校验失败，请刷新页面后重试")
    request.state.user = session.context
    return session


class AuthenticationMiddleware(BaseHTTPMiddleware):
    """现有 HTTP 业务统一登录门禁；资源 owner 检查由 A04～A08 逐项接入。"""
    async def dispatch(self, request, call_next):
        path = request.url.path
        if path.startswith("/api/") and not path.startswith("/api/auth/") and path not in {"/api/app-version","/api/health"}:
            try:
                session = await require_session(request, csrf=request.method not in _SAFE_METHODS)
                if path.startswith(('/api/settings/tdx', '/api/settings/proxy', '/api/settings/llm', '/api/admin/')) and session.account.role != 'admin':
                    raise HTTPException(403, '仅管理员可管理公共配置')
                if session.account.role != 'admin' and (
                    ((path == '/api/models' or (path.startswith('/api/models/') and path != '/api/models/preferences')) and request.method != 'GET')
                    or path.startswith(('/api/usage/limits','/api/usage/reconcile','/api/usage/cloud-confirmation','/api/usage/policy'))
                    or (path == '/api/usage' and request.query_params.get('subject') not in {None,str(session.context.user_id)})
                ):
                    raise HTTPException(403, '仅管理员可管理公共额度或查看其他用户用量')
                if session.account.must_change_password:
                    raise HTTPException(403, {"code": "password_change_required", "message": "请先修改临时密码"})
            except accounts.AccountError as exc:
                return JSONResponse({"detail": {"code": exc.code, "message": str(exc)}}, status_code=exc.status_code, headers={"Cache-Control": "no-store"})
            except HTTPException as exc:
                return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers={"Cache-Control": "no-store"})
            from services.ai_gate import AI_ROUTES,require_route_ready,AIUnavailable
            if (request.method,path.rstrip('/')) in AI_ROUTES:
                try:
                    with user_scope(session.context.user_id):
                        await require_route_ready(path)
                except AIUnavailable as exc:
                    return JSONResponse({'detail':{'code':exc.code,'message':str(exc)}},status_code=503)
            with user_scope(session.context.user_id):
                from services.task_context import _version
                version_token=_version.set(session.context.auth_version)
                try:
                    response = await call_next(request)
                finally:
                    _version.reset(version_token)
                if request.method not in _SAFE_METHODS and path.startswith('/api/settings/') and response.status_code<400:
                    db=await get_db()
                    try:
                        await audit.record(db,action='configuration_changed',outcome='success',created_at=accounts._now(None),actor_id=session.context.user_id)
                        await db.commit()
                    finally:await db.close()
            response.headers["Cache-Control"] = "no-store"
            return response
        response = await call_next(request)
        if path.startswith("/api/auth/"):
            response.headers["Cache-Control"] = "no-store"
        return response
