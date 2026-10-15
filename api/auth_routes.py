"""初始化、登录与密码管理；不提供匿名网页恢复。"""
from __future__ import annotations

from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError

from services import accounts, auth


router = APIRouter(prefix="/api/auth", tags=["auth"])
_MAX_BODY = 16 * 1024


class BootstrapIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: SecretStr
    username: str
    password: SecretStr


class AccountOut(BaseModel):
    id: int
    uuid: str
    username: str
    role: Literal["admin", "user"]
    active: bool
    must_change_password: bool
    auth_version: int
    password_reset_at: int | None


class BootstrapStatus(BaseModel):
    initialized: bool
    credential_available: bool


@router.get("/bootstrap/status", response_model=BootstrapStatus)
async def get_bootstrap_status(response: Response):
    response.headers["Cache-Control"] = "no-store"
    return await accounts.bootstrap_status()


@router.post("/bootstrap", status_code=201, response_model=AccountOut, openapi_extra={
    "requestBody": {"required": True, "content": {"application/json": {"schema": BootstrapIn.model_json_schema()}}},
})
async def bootstrap(request: Request, response: Response):
    origin = request.headers.get("origin")
    if origin is not None and origin != f"{request.url.scheme}://{request.url.netloc}":
        raise HTTPException(403, "请求来源不被允许")
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(415, "初始化请求须使用 JSON")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > _MAX_BODY:
            raise HTTPException(413, "初始化请求过大")
        body.extend(chunk)
    try:
        data = BootstrapIn.model_validate_json(bytes(body))
    except ValidationError:
        # 默认 422 包含 input；此入口会收到凭据和密码，因此只返回固定提示。
        raise HTTPException(422, "初始化请求格式不正确") from None
    try:
        account = await accounts.bootstrap_admin(token=data.token.get_secret_value(), username=data.username,
                                                password=data.password.get_secret_value())
    except accounts.AccountError as exc:
        raise HTTPException(exc.status_code, {"code": exc.code, "message": str(exc)}) from None
    response.headers["Cache-Control"] = "no-store"
    return asdict(account)


class LoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str
    password: SecretStr


class PasswordIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: SecretStr
    new_password: SecretStr


async def _read_secret_body(request: Request, model):
    auth.check_origin(request)
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(415, "请求须使用 JSON")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > _MAX_BODY:
            raise HTTPException(413, "请求过大")
        body.extend(chunk)
    try:
        return model.model_validate_json(bytes(body))
    except ValidationError:
        raise HTTPException(422, "请求格式不正确") from None


def _auth_error(exc):
    return HTTPException(exc.status_code, {"code": exc.code, "message": str(exc)})


@router.post("/login")
async def login(request: Request, response: Response):
    data = await _read_secret_body(request, LoginIn)
    try:
        session = await auth.login(username=data.username, password=data.password.get_secret_value())
    except accounts.AccountError as exc:
        raise _auth_error(exc) from None
    response.set_cookie(auth.COOKIE_NAME, session.token, max_age=auth.SESSION_TTL, httponly=True,
                        secure=request.url.scheme == "https", samesite="strict", path="/")
    response.headers["Cache-Control"] = "no-store"
    return {"account": asdict(session.account), "expires_at": session.expires_at,
            "csrf_token": auth.csrf_token(session.token)}


@router.get("/me")
async def me(request: Request, response: Response):
    try:
        session = await auth.require_session(request)
    except accounts.AccountError as exc:
        raise _auth_error(exc) from None
    response.headers["Cache-Control"] = "no-store"
    return {"account": asdict(session.account), "expires_at": session.expires_at,
            "csrf_token": auth.csrf_token(request.cookies[auth.COOKIE_NAME])}


@router.post("/logout", status_code=204)
async def logout(request: Request):
    try:
        await auth.require_session(request, csrf=True)
        await auth.logout(request.cookies[auth.COOKIE_NAME])
    except accounts.AccountError as exc:
        raise _auth_error(exc) from None
    response = Response(status_code=204, headers={"Cache-Control": "no-store"})
    response.delete_cookie(auth.COOKIE_NAME, httponly=True, secure=request.url.scheme == "https", samesite="strict")
    return response


@router.post("/password", status_code=204)
async def password(request: Request):
    try:
        await auth.require_session(request, csrf=True)
        data = await _read_secret_body(request, PasswordIn)
        await auth.change_password(request.cookies[auth.COOKIE_NAME], current_password=data.current_password.get_secret_value(),
                                   new_password=data.new_password.get_secret_value())
    except accounts.AccountError as exc:
        raise _auth_error(exc) from None
    response = Response(status_code=204, headers={"Cache-Control": "no-store"})
    response.delete_cookie(auth.COOKIE_NAME, httponly=True, secure=request.url.scheme == "https", samesite="strict")
    return response
