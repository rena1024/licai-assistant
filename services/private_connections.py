"""私人接入配置按账号持久化；原子替换且仅运行账号可读写。"""
from __future__ import annotations
import json
import os
import tempfile
from threading import RLock
from services.runtime_paths import get_runtime_paths
from services.user_context import require_user_id

_lock = RLock()


def _path():
    return get_runtime_paths().file('config', f'users/{require_user_id()}/connections.json')


def get(name: str, default=None):
    with _lock:
        path = _path()
        if not path.exists():
            return default
        return json.loads(path.read_text()).get(name, default)


def save(name: str, value):
    with _lock:
        path = _path()
        values = json.loads(path.read_text()) if path.exists() else {}
        values[name] = value
        descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix='.connections-')
        try:
            with os.fdopen(descriptor, 'w') as target:
                json.dump(values, target, ensure_ascii=False)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
