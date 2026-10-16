"""恢复点使用真实隔离 SQLite 和合成媒体，不触及生产数据。"""
import asyncio
import json
import sqlite3
import threading
from contextlib import closing
import pytest
from tests.test_ownership_portfolio import owners


def test_backup_during_writes_is_restorable(owners):
    from services import backup
    from services.runtime_paths import get_runtime_paths
    from services import private_connections
    from services.user_context import user_scope
    import database as db
    a,b,path=owners
    async def seed():
        sid=await db.create_ask_session('A',user_id=a)
        await db.add_ask_message(sid,'user','synthetic',json.dumps({'images':['/api/ask/image/synthetic.png']}),user_id=a)
    asyncio.run(seed())
    get_runtime_paths().file('media',f'{a}/synthetic.png').write_bytes(b'synthetic-png')
    with user_scope(a):private_connections.save('test-secret','synthetic-secret')
    with closing(sqlite3.connect(path)) as connection:
        connection.execute('PRAGMA journal_mode=WAL')
    stopped=threading.Event();started=threading.Event();failures=[]
    def writer():
        try:
            with closing(sqlite3.connect(path)) as connection:
                while not stopped.is_set():
                    connection.execute("INSERT INTO app_config(key,value) VALUES ('tick','1') ON CONFLICT(key) DO UPDATE SET value=value+1")
                    connection.commit()
                    started.set()
                    stopped.wait(.001)
        except BaseException as exc:failures.append(exc)
    thread=threading.Thread(target=writer);thread.start()
    try:
        assert started.wait(5)
        result=backup.create_backup(kind='manual',now=1000000)
    finally:stopped.set();thread.join()
    assert not failures
    point=backup.validate_backup(result['id'])
    assert not (point/'database.sqlite3-wal').exists()
    assert not (point/'database.sqlite3-shm').exists()
    with closing(sqlite3.connect(point/'database.sqlite3')) as connection:
        assert connection.execute('PRAGMA journal_mode').fetchone()[0]=='delete'
        assert connection.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert connection.execute('SELECT COUNT(*) FROM users').fetchone()[0]==2
    assert (point/f'media/{a}/synthetic.png').read_bytes()==b'synthetic-png'
    assert 'synthetic-secret' in (point/f'config/users/{a}/connections.json').read_text()
    assert len(backup.list_backups())==1
    assert 'synthetic-secret' not in json.dumps(backup.list_backups())


def test_failed_snapshot_never_publishes_partial_backup(owners,monkeypatch):
    from services import backup
    good=backup.create_backup(kind='manual',now=1000)
    def fail(*args):raise OSError('synthetic secret must never surface')
    monkeypatch.setattr(backup,'_copy_file',fail)
    from services.runtime_paths import get_runtime_paths
    get_runtime_paths().file('config','synthetic.json').write_text('{}')
    with pytest.raises(backup.BackupError):backup.create_backup(kind='manual',now=2000)
    assert [point['id'] for point in backup.list_backups()]==[good['id']]
    assert not list(get_runtime_paths().backups.glob('.partial-*'))


def test_backup_checks_digest_and_never_follows_symlink(owners,tmp_path):
    from services import backup
    from services.runtime_paths import get_runtime_paths
    result=backup.create_backup(kind='manual',now=1000)
    point=backup.validate_backup(result['id'])
    (point/'database.sqlite3').write_bytes(b'corrupt')
    with pytest.raises(backup.BackupError):backup.validate_backup(result['id'])
    outside=tmp_path/'outside';outside.write_text('keep')
    get_runtime_paths().file('config','bad').symlink_to(outside)
    with pytest.raises(backup.BackupError):backup.create_backup(kind='manual',now=2000)
    assert outside.read_text()=='keep'


def test_retention_preserves_last_valid_recovery_point(owners):
    from services import backup,retention
    first=backup.create_backup(kind='automatic',now=1000)
    retention.prune_backups(now=1000+100*86400)
    assert backup.validate_backup(first['id'])
    second=backup.create_backup(kind='automatic',now=1000+101*86400)
    retention.prune_backups(now=1000+102*86400)
    assert [p['id'] for p in backup.list_backups()]==[second['id']]


def test_startup_catchup_does_not_invent_missed_backups(owners):
    from services import backup
    from datetime import datetime,timezone
    stamp=int(datetime(2026,10,16,10,tzinfo=timezone.utc).timestamp())
    assert backup.backup_if_due(now=stamp,startup=True)
    assert backup.backup_if_due(now=stamp,startup=True) is None
    assert len(backup.list_backups())==1
