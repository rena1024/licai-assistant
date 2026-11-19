"""仅保存当前进程中在途的模型元信息，按登录用户和请求隔离，不保存正文或凭证。"""
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from threading import RLock
from services.user_context import require_user_id

_request_id = ContextVar('llm_activity_request', default='')
_lock = RLock()
_calls = {}
_request_tasks = {}


def user_has_calls(user_id):
    with _lock:
        return any(call['owner_id'] == user_id for call in _calls.values())


def bind_request(task_id):
    request_id = _request_id.get()
    if request_id:
        with _lock:
            _request_tasks[(require_user_id(), request_id)] = task_id


def clear_task_requests(task_id):
    with _lock:
        for key, linked in list(_request_tasks.items()):
            if linked == task_id:
                _request_tasks.pop(key, None)


@contextmanager
def request_scope(value):
    token = _request_id.set(value if re.fullmatch(r'[a-f0-9]{32}', value or '') else '')
    try:
        yield
    finally:
        _request_id.reset(token)


@contextmanager
def track(attempt, context, model):
    with _lock:
        _calls[attempt] = {
            'owner_id': require_user_id(), 'request_id': _request_id.get(),
            'task_id': context.task_id, 'scene': context.scene,
            'model_id': model.id, 'model': model.model, 'local': model.local,
            'status': 'queued' if model.local else 'preparing',
            '_phase_started': time.monotonic(),
        }
    try:
        yield
    finally:
        with _lock:
            _calls.pop(attempt, None)


def set_running(attempt):
    with _lock:
        if attempt in _calls:
            _calls[attempt]['status'] = 'running'
            _calls[attempt]['_phase_started'] = time.monotonic()


def snapshot(*, request_id='', task_id=''):
    owner = require_user_id()
    if not request_id and not task_id:
        return []
    with _lock:
        linked_task = _request_tasks.get((owner, request_id)) if request_id else None
        return [{**{k: v for k, v in call.items() if k not in {'owner_id', 'request_id', '_phase_started'}},
                 'elapsed_seconds': max(0, int(time.monotonic() - call['_phase_started']))}
                for call in _calls.values() if call['owner_id'] == owner
                and (call['task_id'] == (task_id or linked_task) if task_id or linked_task else call['request_id'] == request_id)]
