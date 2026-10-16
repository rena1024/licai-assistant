"""最小本机健康检查，不读取业务正文，不探测模型或通知渠道。"""
import sqlite3
from contextlib import closing
from services.runtime_paths import database_path
from services.runtime_state import read_state
from config import config


def health_status():
    try:
        state=read_state()
        if state.get('maintenance'):return {'status':'maintenance'},503
        path=database_path(config.db_path)
        if not path.is_file():return {'status':'not_ready'},503
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=1)) as db:
            if db.execute('PRAGMA quick_check(1)').fetchone()[0]!='ok':return {'status':'not_ready'},503
        return {'status':'ok'},200
    except Exception:
        return {'status':'not_ready'},503
