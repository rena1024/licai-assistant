"""带摘要与恢复版本的本机恢复点；半成品永不对外发布。"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import time
import uuid
from datetime import datetime,timezone,timedelta
from services.runtime_paths import get_runtime_paths,database_path
from services.runtime_state import file_lock,read_state,update_state,check_generation
from config import config

FORMAT_VERSION=1
KINDS={'automatic','manual','protective'}
_ID=re.compile(r'^(automatic|manual|protective)-[0-9]+-[a-f0-9]{12}$')
_MEDIA=re.compile(r'/api/ask/image/([A-Za-z0-9_.-]+\.(?:png|jpg|jpeg|gif|webp))')


class BackupError(RuntimeError):
    def __init__(self,code='invalid_backup'):
        self.code=code
        message='恢复回滚尚未完成，系统保持维护状态，请检查本机存储后重启以继续恢复' if code=='rollback_requires_recovery' else '备份或恢复操作失败，请检查本机运行状态后重试'
        super().__init__(message)


def _digest(path):
    digest=hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda:source.read(1024*1024),b''):digest.update(chunk)
    return digest.hexdigest()


def _safe_file(path,root):
    if not path.is_relative_to(root) or path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise BackupError('unsafe_path')
    # 连父目录中的软链接也拒绝，不能借链接纳入其他账户文件。
    if any(parent.is_symlink() for parent in path.parents if parent.is_relative_to(root)):
        raise BackupError('unsafe_path')
    return path


def _copy_file(source,target):
    target.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
    shutil.copyfile(source,target)
    target.chmod(0o600)


def _schema(connection):
    rows=dict(connection.execute('SELECT key,value FROM runtime_metadata'))
    required=('layout_version','account_schema_version','portfolio_schema_version','settings_schema_version','ask_schema_version')
    if any(rows.get(key)!='1' for key in required):raise BackupError('incompatible_schema')
    if connection.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise BackupError('invalid_database')
    if connection.execute('PRAGMA foreign_key_check').fetchone():raise BackupError('invalid_database')
    return {key:rows[key] for key in required}


def _metadata(point,manifest):
    return {'id':point.name,'kind':manifest['kind'],'created_at':manifest['created_at'],'file_count':len(manifest['files'])}


def create_backup(*,kind='manual',now=None,maintenance=False):
    if kind not in KINDS:raise ValueError('未知备份类型')
    if not maintenance:check_generation()
    now=int(time.time() if now is None else now)
    paths=get_runtime_paths();paths.prepare()
    identifier=f'{kind}-{now}-{uuid.uuid4().hex[:12]}'
    temporary=Path(tempfile.mkdtemp(prefix='.partial-',dir=paths.backups))
    try:
        with file_lock:
            if not maintenance:check_generation()
            target=temporary/'database.sqlite3'
            source=database_path(config.db_path)
            with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as reader,sqlite3.connect(target) as writer:
                reader.backup(writer)
            target.chmod(0o600)
            with sqlite3.connect(target) as snapshot:
                versions=_schema(snapshot)
                references=set()
                for table,column in (('ask_message','meta'),('ask_run','events'),('morning_briefings','payload_json')):
                    for owner,value in snapshot.execute(f'SELECT user_id,{column} FROM {table}'):
                        for filename in _MEDIA.findall(value or ''):
                            if Path(filename).name!=filename:raise BackupError('invalid_media')
                            references.add(f'media/{owner}/{filename}')
            for name in sorted(references):
                file=_safe_file(paths.root/name,paths.root)
                if not file.is_file():raise BackupError('missing_media')
                _copy_file(file,temporary/name)
            for source_file in paths.config.rglob('*'):
                _safe_file(source_file,paths.root)
                if source_file.is_dir():continue
                if source_file.name=='runtime-state.json' or source_file.name.startswith('.'):continue
                _copy_file(source_file,temporary/source_file.relative_to(paths.root))
            # .env 是恢复必需配置，但运行代际和维护状态永不从快照恢复。
            env=paths.config/'.env'
            if env.exists():_copy_file(_safe_file(env,paths.root),temporary/'config/.env')
            files={file.relative_to(temporary).as_posix():_digest(file) for file in temporary.rglob('*') if file.is_file()}
            manifest={'version':FORMAT_VERSION,'kind':kind,'created_at':now,'schema':versions,'files':files}
            (temporary/'manifest.json').write_text(json.dumps(manifest,sort_keys=True))
            (temporary/'manifest.json').chmod(0o600)
            if not maintenance:check_generation()
            point=paths.backups/identifier
            os.replace(temporary,point)
        return _metadata(point,manifest)
    except Exception as exc:
        shutil.rmtree(temporary,ignore_errors=True)
        update_state(backup_error='backup_failed',backup_error_at=now)
        if isinstance(exc,BackupError):raise
        raise BackupError('storage_failed') from None


def validate_backup(identifier):
    if not isinstance(identifier,str) or not _ID.fullmatch(identifier):raise BackupError('invalid_id')
    root=get_runtime_paths().backups
    point=_safe_file(root/identifier,root)
    try:
        manifest=json.loads(_safe_file(point/'manifest.json',root).read_text())
        if manifest['version']!=FORMAT_VERSION or manifest['kind'] not in KINDS or type(manifest['created_at']) is not int:raise ValueError()
        files=manifest['files']
        if not isinstance(files,dict) or 'database.sqlite3' not in files:raise ValueError()
        for name,digest in files.items():
            relative=Path(name)
            if relative.is_absolute() or '..' in relative.parts or (name!='database.sqlite3' and relative.parts[0] not in {'config','media'}):raise ValueError()
            if name=='config/runtime-state.json':raise ValueError()
            file=_safe_file(point/relative,point)
            if _digest(file)!=digest:raise ValueError()
        actual={file.relative_to(point).as_posix() for file in point.rglob('*') if file.is_file() and file.name!='manifest.json'}
        if actual!=set(files):raise ValueError()
        with sqlite3.connect((point/'database.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
            if _schema(db)!=manifest['schema']:raise ValueError()
        return point
    except (OSError,ValueError,KeyError,TypeError,sqlite3.DatabaseError):
        raise BackupError('invalid_backup') from None


def list_backups():
    paths=get_runtime_paths()
    if not paths.backups.exists():return []
    result=[]
    for point in paths.backups.iterdir():
        if not _ID.fullmatch(point.name):continue
        try:
            valid=validate_backup(point.name)
            result.append(_metadata(valid,json.loads((valid/'manifest.json').read_text())))
        except BackupError:continue
    return sorted(result,key=lambda row:(row['created_at'],row['id']),reverse=True)


def backup_if_due(*,now=None,startup=False):
    now=int(time.time() if now is None else now)
    local=datetime.fromtimestamp(now,timezone(timedelta(hours=8)))
    today=local.date().isoformat()
    if read_state().get('backup_day')==today:return None
    if not startup and local.hour<20:return None
    result=create_backup(kind='automatic',now=now)
    update_state(backup_day=today,backup_success_at=now,backup_error=None)
    from services.retention import prune_backups
    prune_backups(now=now)
    return result
