"""恢复点保留策略；手动和保护备份至少保留最近五份及三十天。"""
import shutil
import time
from services.runtime_state import file_lock


def prune_backups(*,now=None):
    from services.backup import list_backups,validate_backup
    now=int(time.time() if now is None else now)
    with file_lock:
        points=list_backups()
        protected={points[0]['id']} if points else set()
        for kind in ('manual','protective'):
            protected.update(row['id'] for row in [p for p in points if p['kind']==kind][:5])
        for row in points:
            if row['id'] in protected or now-row['created_at']<=30*86400:continue
            shutil.rmtree(validate_backup(row['id']))
