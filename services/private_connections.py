"""私人接入配置按账号持久化；原子替换且仅运行账号可读写。"""
from __future__ import annotations
import json
import os
import tempfile
from services.runtime_state import file_lock,check_generation
from services.runtime_paths import get_runtime_paths
from services.user_context import require_user_id

_lock = file_lock


def _path():
    paths = get_runtime_paths()
    return paths.validate(paths.config / 'users' / str(require_user_id()) / 'connections.json')


def get(name: str, default=None):
    with _lock:
        check_generation()
        path = _path()
        if not path.exists():
            return default
        return json.loads(path.read_text()).get(name, default)


def save(name: str, value):
    with _lock:
        check_generation()
        from services.task_context import ensure_active_sync
        ensure_active_sync()
        path = _path()
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
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
