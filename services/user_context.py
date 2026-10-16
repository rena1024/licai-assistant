"""在请求／服务之间传递已认证身份；不提供匿名或管理员默认身份。"""
from contextlib import contextmanager
from contextvars import ContextVar

_current_user_id: ContextVar[int | None] = ContextVar('licai_user_id', default=None)


def validate_user_id(user_id: int) -> int:
    if type(user_id) is not int or user_id <= 0:
        raise ValueError('业务操作必须提供有效用户身份')
    return user_id


def require_user_id() -> int:
    return validate_user_id(_current_user_id.get())


@contextmanager
def user_scope(user_id: int):
    """仅供认证入口和持有明确 owner 的内部任务使用，不从请求正文建立身份。"""
    token = _current_user_id.set(validate_user_id(user_id))
    try:
        yield
    finally:
        _current_user_id.reset(token)
