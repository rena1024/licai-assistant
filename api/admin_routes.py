"""仅管理员可用的账号清单与生命周期管理。"""
from dataclasses import asdict
from fastapi import APIRouter, HTTPException, Request
from services import accounts
from services.user_context import require_user_id
from api.auth_routes import _read_secret_body, _auth_error
from pydantic import BaseModel, ConfigDict, SecretStr, Field

router = APIRouter(prefix='/api/admin/users', tags=['admin'])


class NewUser(BaseModel):
    model_config = ConfigDict(extra='forbid')
    username: str
    password: SecretStr


class ResetPassword(BaseModel):
    model_config = ConfigDict(extra='forbid')
    password: SecretStr


class ActiveState(BaseModel):
    model_config = ConfigDict(extra='forbid')
    active: bool


class DeleteUser(BaseModel):
    model_config = ConfigDict(extra='forbid')
    confirmation_username: str = Field(min_length=3, max_length=64)


@router.get('')
async def list_users():
    try:
        from services.account_deletion import pending_cleanups
        return {'users': [asdict(user) for user in await accounts.list_users(actor_id=require_user_id())],
                'pending_deletions': await pending_cleanups(actor_id=require_user_id())}
    except accounts.AccountError as exc:
        raise _auth_error(exc) from None


@router.post('')
async def create_user(request: Request):
    data = await _read_secret_body(request, NewUser)
    try:
        return asdict(await accounts.create_user(actor_id=require_user_id(), username=data.username, password=data.password.get_secret_value()))
    except accounts.AccountError as exc:
        raise _auth_error(exc) from None


@router.post('/{user_id}/password')
async def reset_password(user_id: int, request: Request):
    data = await _read_secret_body(request, ResetPassword)
    try:
        return asdict(await accounts.reset_user_password(actor_id=require_user_id(), user_id=user_id, password=data.password.get_secret_value()))
    except accounts.AccountError as exc:
        raise _auth_error(exc) from None


@router.put('/{user_id}/active')
async def set_active(user_id: int, data: ActiveState):
    try:
        return asdict(await accounts.set_user_active(actor_id=require_user_id(), user_id=user_id, active=data.active))
    except accounts.AccountError as exc:
        raise _auth_error(exc) from None


@router.delete('/{user_id}')
async def delete_user(user_id: int, data: DeleteUser):
    try:
        return await accounts.delete_user(actor_id=require_user_id(), user_id=user_id,
                                          confirmation_username=data.confirmation_username)
    except accounts.AccountError as exc:
        raise _auth_error(exc) from None


@router.post('/{user_id}/deletion-cleanup')
async def retry_deletion_cleanup(user_id: int):
    from services.account_deletion import retry_cleanup
    try:
        return await retry_cleanup(actor_id=require_user_id(), user_id=user_id)
    except accounts.AccountError as exc:
        raise _auth_error(exc) from None
