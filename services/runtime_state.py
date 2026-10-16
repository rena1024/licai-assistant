"""单进程运行屏障：持久维护状态与单调代际不随数据库恢复回退。"""
from __future__ import annotations
import json
import os
import tempfile
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Condition, RLock
from services.runtime_paths import get_runtime_paths

_generation = ContextVar('runtime_generation',default=None)
_privileged = ContextVar('runtime_maintenance_owner',default=False)
_condition = Condition(RLock())
_active: dict[str,int] = {}
file_lock = RLock()


class RuntimeUnavailable(RuntimeError):
    def __init__(self,code='maintenance'):
        self.code=code
        super().__init__('系统正在维护或任务已失效，请稍后重新登录')


def read_state():
    path=get_runtime_paths().validate(get_runtime_paths().config/'runtime-state.json')
    if not path.exists():return {'generation':0,'maintenance':False,'cloud_hold':True,'trace_until':0}
    try:
        state=json.loads(path.read_text())
        if type(state.get('generation')) is not int or state['generation']<0:raise ValueError()
        return state
    except (OSError,ValueError,TypeError):
        raise RuntimeUnavailable('invalid_runtime_state') from None


def update_state(**changes):
    with _condition:
        path=get_runtime_paths().file('config','runtime-state.json')
        state={**read_state(),**changes}
        descriptor,name=tempfile.mkstemp(dir=path.parent,prefix='.runtime-state-')
        try:
            with os.fdopen(descriptor,'w') as target:
                json.dump(state,target);target.flush();os.fsync(target.fileno())
            os.replace(name,path)
        finally:
            if os.path.exists(name):os.unlink(name)
        _condition.notify_all()
        return state


def check_generation(expected=None):
    with _condition:
        state=read_state()
        if _privileged.get():return state['generation']
        if state.get('maintenance'):raise RuntimeUnavailable()
        observed=_generation.get() if expected is None else expected
        if observed is not None and observed != state['generation']:raise RuntimeUnavailable('stale_generation')
        if _generation.get() is None:_generation.set(state['generation'])
        return state['generation']


@contextmanager
def runtime_scope(*,privileged=False):
    token=_generation.set(None)
    privilege=_privileged.set(privileged)
    try:
        check_generation()
        yield
    finally:
        _privileged.reset(privilege);_generation.reset(token)


def acquire_connection():
    with _condition:
        generation=check_generation()
        key=str(get_runtime_paths().root)
        _active[key]=_active.get(key,0)+1
        return key,generation


def release_connection(key):
    with _condition:
        _active[key]-=1
        _condition.notify_all()


def wait_for_drain(timeout):
    key=str(get_runtime_paths().root)
    with _condition:
        return _condition.wait_for(lambda:_active.get(key,0)==0,timeout=timeout)


def enter_maintenance():
    with _condition:
        state=read_state()
        if state.get('maintenance'):raise RuntimeUnavailable()
        return update_state(maintenance=True,generation=state['generation']+1,cloud_hold=True)
