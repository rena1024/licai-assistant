"""受保护本机恢复；校验与保护失败不覆盖，代际状态不在备份中回退。"""
from __future__ import annotations
import asyncio
import json
import os
from pathlib import Path
import shutil
import sqlite3
from contextlib import closing
import tempfile
import threading
import time
from services import backup,runtime_state
from services.runtime_paths import get_runtime_paths,database_path
from config import config

_restore_lock=threading.Lock()


def create_protection(**kwargs):
    return backup.create_backup(kind='protective',maintenance=True,**kwargs)


def _files_under(area):
    paths=get_runtime_paths()
    root=paths.root/area
    result=[]
    for file in root.rglob('*'):
        backup._safe_file(file,paths.root)
        if file.is_file() and not (area=='config' and file.name=='runtime-state.json'):
            result.append(file.relative_to(paths.root).as_posix())
    return result


def _rollback_files(stage,journal):
    paths=get_runtime_paths()
    for name in journal['incoming']:
        if name not in journal['originals']:
            target=backup._safe_file(paths.root/name,paths.root)
            if target.exists():target.unlink()
    for name in journal['originals']:
        backup._copy_file(stage/'previous'/name,backup._safe_file(paths.root/name,paths.root))
    backup._copy_file(stage/'previous-database.sqlite3',stage/'rollback.sqlite3')
    source=database_path(config.db_path)
    os.replace(stage/'rollback.sqlite3',source)
    for suffix in ('-wal','-shm'):
        side=Path(str(source)+suffix)
        if side.exists():side.unlink()


def _replace_current(point,actor_id):
    paths=get_runtime_paths()
    with runtime_state.file_lock:
        point=backup.validate_backup(point.name)
        protection=create_protection()
        protected=backup.validate_backup(protection['id'])
        stage=Path(tempfile.mkdtemp(prefix='.restore-',dir=paths.root))
        installing=False
        retain_stage=False
        try:
            manifest=json.loads((point/'manifest.json').read_text())
            for name in manifest['files']:backup._copy_file(point/name,stage/name)
            source=database_path(config.db_path)
            with closing(sqlite3.connect(source)) as current:
                local={row[0] for row in current.execute("SELECT u.uuid FROM users u JOIN user_settings s ON s.user_id=u.id WHERE s.key='ai_mode' AND s.value='local_only'")}
            with closing(sqlite3.connect(stage/'database.sqlite3')) as restored:
                with closing(sqlite3.connect(source)) as current:
                    from services.llm_recovery import preserve_finance
                    preserve_finance(current,restored)
                restored.execute('UPDATE sessions SET revoked_at=? WHERE revoked_at IS NULL',(int(time.time()),))
                restored.execute('UPDATE users SET auth_version=auth_version+1')
                restored.execute("UPDATE ask_run SET status='interrupted' WHERE status='running'")
                restored.execute("DELETE FROM user_settings WHERE key='automatic_ai_enabled'")
                for uid,identity in restored.execute('SELECT id,uuid FROM users').fetchall():
                    if identity in local:
                        restored.execute("INSERT INTO user_settings(user_id,key,value) VALUES (?,'ai_mode','local_only') ON CONFLICT(user_id,key) DO UPDATE SET value='local_only'",(uid,))
                restored.commit()
                backup._schema(restored)
            originals=_files_under('config')+_files_under('media')
            incoming=[name for name in manifest['files'] if name!='database.sqlite3']
            # 复制完成后才开始发布；原文件在这一步仍完全没动。
            for name in originals:backup._copy_file(paths.root/name,stage/'previous'/name)
            backup._copy_file(protected/'database.sqlite3',stage/'previous-database.sqlite3')
            journal={'stage':stage.name,'originals':originals,'incoming':incoming,'protection':protection['id']}
            runtime_state.update_state(restore_journal=journal)
            installing=True
            for name in incoming:
                target=backup._safe_file(paths.root/name,paths.root)
                target.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
                os.replace(stage/name,target)
            for name in set(originals)-set(incoming):(paths.root/name).unlink()
            os.replace(stage/'database.sqlite3',source)
            for suffix in ('-wal','-shm'):
                side=Path(str(source)+suffix)
                if side.exists():side.unlink()
            runtime_state.update_state(restore_journal=None,restore_recovery_required=False,maintenance=False,
                                       cloud_hold=True,notification_hold=True,last_restore_at=int(time.time()))
            return protection['id']
        except BaseException:
            if installing:
                try:
                    _rollback_files(stage,journal)
                    runtime_state.update_state(restore_journal=None,restore_recovery_required=False,maintenance=False)
                except BaseException:
                    retain_stage=True
                    runtime_state.update_state(maintenance=True,restore_recovery_required=True)
                    raise backup.BackupError('rollback_requires_recovery') from None
            raise
        finally:
            if not retain_stage:shutil.rmtree(stage,ignore_errors=True)


def recover_interrupted_restore():
    """启动时先回滚未完成发布；失败继续保持维护，禁止运行旧worker。"""
    state=runtime_state.read_state()
    if not state.get('maintenance'):return
    journal=state.get('restore_journal')
    if journal:
        stage_name=journal.get('stage','')
        if not stage_name.startswith('.restore-') or Path(stage_name).name!=stage_name:
            raise backup.BackupError('invalid_restore_journal')
        paths=get_runtime_paths()
        stage=backup._safe_file(paths.root/stage_name,paths.root)
        with runtime_state.file_lock:
            _rollback_files(stage,journal)
            # 先持久确认回滚完成；状态写入失败时保留副本供下次启动重试。
            runtime_state.update_state(maintenance=False,restore_journal=None,restore_recovery_required=False,cloud_hold=True)
            shutil.rmtree(stage,ignore_errors=True)
        return
    runtime_state.update_state(maintenance=False,restore_journal=None,restore_recovery_required=False,cloud_hold=True)



def _reload_configuration():
    """恢复已加载的运行配置，不探测网络或调用模型。环境变量仍由下次进程启动加载。"""
    from services import proxy_config,tdx_client
    with closing(sqlite3.connect(database_path(config.db_path))) as connection:
        values=dict(connection.execute('SELECT key,value FROM app_config'))
    proxy_config.configure(os.environ.get('CRYPTO_PROXY') or values.get('network_proxy',''))
    tdx_client.configure(os.environ.get('TDX_BASE_URL') or values.get('tdx_base_url') or getattr(config,'tdx_base_url',''))



def _clear_caches():
    import sys
    for name,module in list(sys.modules.items()):
        if not name.startswith(('services.','api.')) or module is None:continue
        for key,value in list(vars(module).items()):
            if isinstance(value,dict) and ('cache' in key.lower() or key in {'_RUNS','_instances','_hk_refreshed'}):value.clear()


async def restore_backup(identifier,*,actor_id,drain_timeout=10):
    from database import get_db
    from services.accounts import _require_admin
    point=backup.validate_backup(identifier)
    db=await get_db()
    try:await _require_admin(db,actor_id)
    finally:await db.close()
    if not _restore_lock.acquire(blocking=False):raise runtime_state.RuntimeUnavailable()
    entered=False
    worker=None
    try:
        runtime_state.enter_maintenance();entered=True
        from services import ask_runs,scheduler
        tasks=[run.task for run in list(ask_runs._RUNS.values()) if run.task and not run.task.done()]
        tasks.extend(task for group in list(scheduler._running.values()) for task in list(group))
        for task in tasks:task.cancel()
        from api.ws import _clients,disconnect_user
        for uid in {connection.user_id for connection in list(_clients.values())}:await disconnect_user(uid)
        if not await asyncio.to_thread(runtime_state.wait_for_drain,drain_timeout):raise runtime_state.RuntimeUnavailable('writers_busy')
        worker=asyncio.create_task(asyncio.to_thread(_replace_current,point,actor_id))
        cancelled=False
        # 请求断开只取消等待者，不得提前解除仍在运行的恢复线程屏障。
        while True:
            try:
                protection=await asyncio.shield(worker)
                break
            except asyncio.CancelledError:
                cancelled=True
                if worker.done():
                    protection=worker.result()
                    break
        _clear_caches()
        _reload_configuration()
        if cancelled:raise asyncio.CancelledError()
        return {'restored':True,'protection_id':protection,'reauthentication_required':True,'cloud_hold':True}
    finally:
        if entered and not runtime_state.read_state().get('restore_recovery_required'):
            runtime_state.update_state(maintenance=False)
        _restore_lock.release()
