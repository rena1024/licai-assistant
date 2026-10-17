import asyncio
import sqlite3
from contextvars import copy_context
import pytest
from tests.test_ownership_portfolio import owners


def test_restore_failure_keeps_current_db_and_cloud_hold_survives_restart(owners,monkeypatch):
    from services import backup,restore,runtime_state,auth
    import database as db
    a,b,path=owners
    async def exercise():
        await db.add_holding('600000','before',100,10,user_id=a)
        await db.set_config('network_proxy','http://127.0.0.1:19999')
        point=await asyncio.to_thread(backup.create_backup,kind='manual')
        await db.update_holding('600000',shares=200,user_id=a)
        await db.set_config('network_proxy','http://127.0.0.1:19998')
        session=await auth.login(username='owner-a',password='Synthetic-password-01!')
        stale=copy_context()
        await restore.restore_backup(point['id'],actor_id=a)
        # 旧请求上下文不能读写恢复后的数据库。
        with pytest.raises(runtime_state.RuntimeUnavailable):
            await stale.run(asyncio.create_task,db.add_holding('600001','late',1,1,user_id=a))
        from services.chart_render import save_png
        with pytest.raises(runtime_state.RuntimeUnavailable):stale.run(save_png,b'synthetic-old-image')
        with runtime_state.runtime_scope():
            assert (await db.get_holding('600000',user_id=a))['shares']==100
            with pytest.raises(auth.AuthError):await auth.authenticate(session.token)
        from services import proxy_config
        assert proxy_config.get_proxy()=='http://127.0.0.1:19999'
        assert runtime_state.read_state()['cloud_hold'] is True
        assert runtime_state.read_state()['maintenance'] is False
        return point['id']
    point=asyncio.run(exercise())
    assert runtime_state.read_state()['cloud_hold'] is True
    assert any(p['kind']=='protective' for p in backup.list_backups())


def test_corruption_or_failed_protection_never_overwrites_current_state(owners,monkeypatch):
    from services import backup,restore,runtime_state
    a,b,path=owners
    point=backup.create_backup(kind='manual')
    before=path.read_bytes()
    def fail(**kwargs):raise backup.BackupError('storage_failed')
    monkeypatch.setattr(restore,'create_protection',fail)
    with pytest.raises(backup.BackupError):asyncio.run(restore.restore_backup(point['id'],actor_id=a))
    assert path.read_bytes()==before
    assert not runtime_state.read_state()['maintenance']
    (backup.validate_backup(point['id'])/'database.sqlite3').write_bytes(b'broken')
    with pytest.raises(backup.BackupError):asyncio.run(restore.restore_backup(point['id'],actor_id=a))
    assert path.read_bytes()==before


def test_held_connection_blocks_restore_and_stale_writes(owners):
    from services import backup,restore,runtime_state
    import database as db
    a,b,path=owners
    point=backup.create_backup(kind='manual')
    async def exercise():
        connection=await db.get_db()
        try:
            with pytest.raises(runtime_state.RuntimeUnavailable):
                await restore.restore_backup(point['id'],actor_id=a,drain_timeout=.01)
        finally:await connection.close()
        assert not runtime_state.read_state()['maintenance']
    asyncio.run(exercise())


def test_partial_file_publication_failure_rolls_back_every_original_file(owners,monkeypatch):
    from services import backup,restore,runtime_state
    from services.runtime_paths import get_runtime_paths
    a,b,path=owners
    paths=get_runtime_paths()
    first=paths.file('config','first.json');first.write_text('first-before')
    second=paths.file('config','second.json');second.write_text('second-before')
    point=backup.create_backup(kind='manual')
    first.write_text('first-current');second.write_text('second-current')
    original=restore.os.replace
    failed=False
    def fail(source,target):
        nonlocal failed
        if not failed and str(target)==str(second):
            failed=True
            raise OSError('synthetic install failure')
        return original(source,target)
    monkeypatch.setattr(restore.os,'replace',fail)
    with pytest.raises((OSError,backup.BackupError)):
        asyncio.run(restore.restore_backup(point['id'],actor_id=a))
    assert first.read_text()=='first-current' and second.read_text()=='second-current'
    assert not runtime_state.read_state()['maintenance']


def test_failure_while_moving_second_original_never_deletes_it(owners,monkeypatch):
    from services import backup,restore
    from services.runtime_paths import get_runtime_paths
    a,b,path=owners
    paths=get_runtime_paths()
    paths.file('config','first.json').write_text('first')
    second=paths.file('config','second.json');second.write_text('second')
    point=backup.create_backup(kind='manual')
    original=restore.os.replace
    def fail(source,target):
        if str(source)==str(second) and 'previous-config' in str(target):raise OSError('synthetic failed original move')
        return original(source,target)
    monkeypatch.setattr(restore.os,'replace',fail)
    try:asyncio.run(restore.restore_backup(point['id'],actor_id=a))
    except OSError:pass
    assert second.read_text()=='second'


def test_cancelled_restore_waits_for_worker_before_releasing_barrier(owners,monkeypatch):
    import threading
    from services import backup,restore,runtime_state
    a,b,path=owners
    point=backup.create_backup(kind='manual')
    started=threading.Event();release=threading.Event()
    original=restore._replace_current
    def blocked(*args):
        started.set()
        assert release.wait(5)
        return original(*args)
    monkeypatch.setattr(restore,'_replace_current',blocked)
    async def exercise():
        task=asyncio.create_task(restore.restore_backup(point['id'],actor_id=a))
        assert await asyncio.to_thread(started.wait,5)
        task.cancel()
        await asyncio.sleep(0)
        assert runtime_state.read_state()['maintenance']
        assert restore._restore_lock.locked()
        release.set()
        with pytest.raises(asyncio.CancelledError):await task
        assert not runtime_state.read_state()['maintenance']
        assert not restore._restore_lock.locked()
    asyncio.run(exercise())


def test_failed_rollback_stays_in_maintenance_and_startup_repairs_it(owners,monkeypatch):
    from services import backup,restore,runtime_state
    from services.runtime_paths import get_runtime_paths
    a,b,path=owners
    paths=get_runtime_paths();file=paths.file('config','first.json');file.write_text('snapshot')
    point=backup.create_backup(kind='manual');file.write_text('current')
    original_replace=restore.os.replace;original_rollback=restore._rollback_files
    def install_fail(source,target):
        if str(target)==str(file):raise OSError('synthetic publish failure')
        return original_replace(source,target)
    def rollback_fail(*args):raise OSError('synthetic rollback failure')
    monkeypatch.setattr(restore.os,'replace',install_fail)
    monkeypatch.setattr(restore,'_rollback_files',rollback_fail)
    with pytest.raises(backup.BackupError):asyncio.run(restore.restore_backup(point['id'],actor_id=a))
    assert runtime_state.read_state()['maintenance']
    assert runtime_state.read_state()['restore_recovery_required']
    monkeypatch.setattr(restore.os,'replace',original_replace)
    monkeypatch.setattr(restore,'_rollback_files',original_rollback)
    original_update=runtime_state.update_state
    def status_failure(**changes):
        if changes.get('restore_journal',False) is None:raise OSError('synthetic state write failure')
        return original_update(**changes)
    monkeypatch.setattr(runtime_state,'update_state',status_failure)
    with pytest.raises(OSError):restore.recover_interrupted_restore()
    journal=runtime_state.read_state()['restore_journal']
    assert (paths.root/journal['stage']).is_dir()
    monkeypatch.setattr(runtime_state,'update_state',original_update)
    restore.recover_interrupted_restore()
    assert file.read_text()=='current'
    assert not runtime_state.read_state()['maintenance']


def test_price_monitor_takes_new_generation_after_restore(owners,monkeypatch):
    from api import ws
    from services import runtime_state
    seen=[]
    async def tick():seen.append(runtime_state.check_generation())
    async def pause(_):
        if len(seen)==1:
            state=runtime_state.read_state()
            runtime_state.update_state(generation=state['generation']+1)
        else:raise asyncio.CancelledError()
        if runtime_state.read_state()['generation']>3:raise asyncio.CancelledError()
    monkeypatch.setattr(ws,'push_prices_once',tick)
    monkeypatch.setattr(ws.asyncio,'sleep',pause)
    async def exercise():
        with runtime_state.runtime_scope():
            with pytest.raises(asyncio.CancelledError):await ws.price_monitor_loop()
    asyncio.run(exercise())
    assert len(seen)==2 and seen[1]==seen[0]+1


def test_repeated_close_cancellation_keeps_connection_lease_until_closed(owners,monkeypatch):
    import aiosqlite
    import database
    from services import runtime_state
    original=aiosqlite.Connection.close
    async def exercise():
        started=asyncio.Event();release=asyncio.Event()
        async def blocked(connection):
            started.set();await release.wait();await original(connection)
        monkeypatch.setattr(aiosqlite.Connection,'close',blocked)
        connection=await database.get_db()
        task=asyncio.create_task(connection.close())
        try:
            await started.wait()
            task.cancel();await asyncio.sleep(0)
            task.cancel()
            # 推进取消传播链，不依赖真实时钟或线程速度。
            for _ in range(3):await asyncio.sleep(0)
            assert not runtime_state.wait_for_drain(0)
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):await task
            await original(connection)
        assert runtime_state.wait_for_drain(0)
    asyncio.run(exercise())
