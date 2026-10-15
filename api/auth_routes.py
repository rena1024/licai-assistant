"""初始化入口；登录和会话交互在 A03 接入，不提供匿名密码恢复。"""
from __future__ import annotations

from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError

from services import accounts


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
