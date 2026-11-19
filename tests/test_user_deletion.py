import asyncio
import sqlite3
import pytest
from tests.test_ownership_portfolio import owners
from services import accounts
from services.user_context import user_scope


def test_delete_user_removes_private_data_and_keeps_financial_identity(owners):
    import database as db
    from services import auth, budget, private_connections, notification_delivery, search
    from services.llm_types import CallContext, Usage
    from services.runtime_paths import get_runtime_paths
    from tests.test_budget_ledger import PRICE
    a, b, path = owners

    async def exercise():
        session = await auth.login(username='owner-b', password='Synthetic-password-02!')
        await db.add_holding('600000', 'A', 100, 10, user_id=a)
        await db.add_holding('600000', 'B', 100, 10, user_id=b)
        await db.add_position_action('600000', 'BUY', 10, 100, user_id=b)
        asset = await db.add_external_asset('CASH', 'cash', 'B现金', '', 100, user_id=b)
        await db.add_external_action(asset, 'DEPOSIT', amount=100, user_id=b)
        await db.add_dca_schedule(asset, 'amount', 10, user_id=b)
        sid = await db.create_ask_session('私人问答', user_id=b)
        await db.add_ask_message(sid, 'user', '私人内容', user_id=b)
        with user_scope(b):
            private_connections.save('synthetic', {'key': 'private-key'})
            await notification_delivery.save_channel({'kind': 'pushplus', 'name': '私人通知', 'config': {'token': 'synthetic'}}, expected_version=0)
            await search.preference(True)
        with user_scope(a):
            await budget.set_limits(0, day='2', month='2')
            await budget.set_limits(b, day='2', month='2')
        with user_scope(b):
            rid = await budget.reserve_attempt(CallContext(b, 'ask', 'retained'), 'synthetic', PRICE, 100, 'retained')
            await budget.settle_attempt(rid, Usage(input_tokens=1, output_tokens=0))
        with sqlite3.connect(path) as connection:
            prior_totals = connection.execute('select * from llm_budget_totals order by subject,kind,period').fetchall()
        media = get_runtime_paths().file('media', f'{b}/image.png')
        media.write_bytes(b'synthetic')
        original = (await accounts.list_users(actor_id=a))[1]
        await accounts.set_user_active(actor_id=a, user_id=b, active=False)
        result = await accounts.delete_user(actor_id=a, user_id=b, confirmation_username='owner-b')
        assert result == {'deleted': True, 'files_pending': False}
        assert [u.id for u in await accounts.list_users(actor_id=a)] == [a]
        assert not media.parent.exists()
        assert not (get_runtime_paths().config / 'users' / str(b)).exists()
        with pytest.raises(auth.AuthError):
            await auth.authenticate(session.token)
        with sqlite3.connect(path) as connection:
            assert connection.execute('select uuid from users where id=?', (b,)).fetchone()[0] == original.uuid
            assert connection.execute('select username from users where id=?', (b,)).fetchone()[0] != 'owner-b'
            for table in ('holdings', 'position_actions', 'external_assets', 'external_asset_actions', 'ask_session', 'ask_message', 'sessions', 'brokers', 'notification_channels', 'search_preferences'):
                assert connection.execute(f'SELECT COUNT(*) FROM {table} WHERE user_id=?', (b,)).fetchone()[0] == 0
            assert connection.execute('select count(*) from dca_schedules').fetchone()[0] == 0
            assert connection.execute('select count(*) from notification_channel_versions').fetchone()[0] == 0
            assert connection.execute('select count(*) from llm_attempts where owner_id=?', (b,)).fetchone()[0] == 1
            assert connection.execute('select * from llm_budget_totals order by subject,kind,period').fetchall() == prior_totals
            assert connection.execute("select count(*) from audit_events where action='user_deleted' and target_id=?", (b,)).fetchone()[0] == 1
            assert not connection.execute('pragma foreign_key_check').fetchall()
        assert await db.get_holding('600000', user_id=a)
        replacement = await accounts.create_user(actor_id=a, username='owner-b', password='Synthetic-new-01!')
        assert replacement.id != b and replacement.uuid != original.uuid
    asyncio.run(exercise())


def test_deletion_blocks_inflight_requests_and_late_file_writes(owners):
    import database as db
    from services import private_connections
    from services.chart_render import save_png
    from services.runtime_paths import get_runtime_paths
    a, b, _ = owners
    async def exercise():
        await accounts.set_user_active(actor_id=a, user_id=b, active=False)
        connection = await db.get_db()
        try:
            await connection.execute("INSERT INTO search_attempts VALUES ('inflight',?,'synthetic','2026-10', 'tavily',1,'reserved',0)", (b,))
            await connection.commit()
        finally:
            await connection.close()
        with pytest.raises(accounts.AccountError) as caught:
            await accounts.delete_user(actor_id=a, user_id=b, confirmation_username='owner-b')
        assert caught.value.code == 'user_busy'
        assert len(await accounts.list_users(actor_id=a)) == 2
        connection = await db.get_db()
        try:
            await connection.execute("UPDATE search_attempts SET state='unknown' WHERE id='inflight'")
            await connection.commit()
        finally:
            await connection.close()
        await accounts.delete_user(actor_id=a, user_id=b, confirmation_username='owner-b')
        with user_scope(b):
            with pytest.raises(accounts.AccountError):
                private_connections.save('late', 'synthetic')
            with pytest.raises(accounts.AccountError):
                save_png(b'synthetic')
        assert not (get_runtime_paths().config / 'users' / str(b)).exists()
        assert not (get_runtime_paths().media / str(b)).exists()
    asyncio.run(exercise())


def test_cleanup_rejects_symlink_to_another_account(owners):
    from services.runtime_paths import get_runtime_paths
    a, b, _ = owners
    paths = get_runtime_paths()
    protected = paths.file('media', f'{a}/keep.png')
    protected.write_bytes(b'keep')
    (paths.media / str(b)).symlink_to(protected.parent, target_is_directory=True)
    async def exercise():
        await accounts.set_user_active(actor_id=a, user_id=b, active=False)
        result = await accounts.delete_user(actor_id=a, user_id=b, confirmation_username='owner-b')
        assert result['files_pending']
        assert protected.read_bytes() == b'keep'
    asyncio.run(exercise())


def test_delete_user_guards_and_deleted_account_cannot_be_reenabled(owners):
    a, b, _ = owners
    async def exercise():
        for actor, target, confirmation, code in [
            (b, a, 'owner-a', 'forbidden'), (a, a, 'owner-a', 'forbidden'),
            (a, b, 'owner-b', 'user_active'), (a, 999, 'missing', 'account_missing'),
        ]:
            with pytest.raises(accounts.AccountError) as caught:
                await accounts.delete_user(actor_id=actor, user_id=target, confirmation_username=confirmation)
            assert caught.value.code == code
        await accounts.set_user_active(actor_id=a, user_id=b, active=False)
        with pytest.raises(accounts.AccountError) as caught:
            await accounts.delete_user(actor_id=a, user_id=b, confirmation_username='wrong')
        assert caught.value.code == 'confirmation_mismatch'
        await accounts.delete_user(actor_id=a, user_id=b, confirmation_username='owner-b')
        for action in [accounts.set_user_active(actor_id=a, user_id=b, active=True), accounts.reset_user_password(actor_id=a, user_id=b, password='Synthetic-new-01!')]:
            with pytest.raises(accounts.AccountError) as caught:
                await action
            assert caught.value.code == 'account_missing'
    asyncio.run(exercise())


def test_file_cleanup_failure_is_visible_and_retryable(owners, monkeypatch):
    from services import account_deletion
    from services.runtime_paths import get_runtime_paths
    a, b, _ = owners
    folder = get_runtime_paths().file('config', f'users/{b}/connections.json')
    folder.write_text('{"synthetic":"credential"}')
    actual = account_deletion.shutil.rmtree
    def fail(*args, **kwargs):
        raise PermissionError('synthetic')
    async def exercise():
        await accounts.set_user_active(actor_id=a, user_id=b, active=False)
        monkeypatch.setattr(account_deletion.shutil, 'rmtree', fail)
        assert (await accounts.delete_user(actor_id=a, user_id=b, confirmation_username='owner-b'))['files_pending']
        assert await account_deletion.pending_cleanups(actor_id=a) == [{'id': b}]
        monkeypatch.setattr(account_deletion.shutil, 'rmtree', actual)
        assert not (await account_deletion.retry_cleanup(actor_id=a, user_id=b))['files_pending']
        assert not folder.parent.exists()
        assert await account_deletion.pending_cleanups(actor_id=a) == []
    asyncio.run(exercise())


def test_delete_api_requires_admin_disabled_target_and_exact_confirmation(owners):
    from fastapi.testclient import TestClient
    from run import app
    a, b, _ = owners
    with TestClient(app) as client:
        login = client.post('/api/auth/login', json={'username': 'owner-b', 'password': 'Synthetic-password-02!'})
        client.headers['X-CSRF-Token'] = login.json()['csrf_token']
        assert client.request('DELETE', f'/api/admin/users/{a}', json={'confirmation_username': 'owner-a'}).status_code == 403
        login = client.post('/api/auth/login', json={'username': 'owner-a', 'password': 'Synthetic-password-01!'})
        client.headers['X-CSRF-Token'] = login.json()['csrf_token']
        assert client.request('DELETE', f'/api/admin/users/{a}', json={'confirmation_username': 'owner-a'}).status_code == 403
        assert client.request('DELETE', f'/api/admin/users/{b}', json={'confirmation_username': 'owner-b'}).status_code == 409
        assert client.put(f'/api/admin/users/{b}/active', json={'active': False}).status_code == 200
        assert client.request('DELETE', f'/api/admin/users/{b}', json={'confirmation_username': 'wrong'}).status_code == 422
        response = client.request('DELETE', f'/api/admin/users/{b}', json={'confirmation_username': 'owner-b'})
        assert response.status_code == 200 and response.json()['deleted']
        assert [u['id'] for u in client.get('/api/admin/users').json()['users']] == [a]
        assert client.put(f'/api/admin/users/{b}/active', json={'active': True}).status_code == 404


def test_database_rollback_keeps_private_files_and_user_data(owners, monkeypatch):
    import database as db
    from services import audit
    from services.runtime_paths import get_runtime_paths
    a, b, _ = owners
    file = get_runtime_paths().file('media', f'{b}/keep.png')
    file.write_bytes(b'keep')
    actual = audit.record
    async def fail(connection, **kwargs):
        if kwargs['action'] == 'user_deleted' and kwargs['outcome'] == 'success':
            raise sqlite3.OperationalError('synthetic rollback')
        await actual(connection, **kwargs)
    async def exercise():
        await db.add_holding('600000', 'B', 100, 10, user_id=b)
        await accounts.set_user_active(actor_id=a, user_id=b, active=False)
        monkeypatch.setattr(audit, 'record', fail)
        with pytest.raises(sqlite3.OperationalError):
            await accounts.delete_user(actor_id=a, user_id=b, confirmation_username='owner-b')
        assert file.read_bytes() == b'keep'
        assert await db.get_holding('600000', user_id=b)
        assert [user.username for user in await accounts.list_users(actor_id=a)] == ['owner-a', 'owner-b']
    asyncio.run(exercise())


def test_old_backup_restore_does_not_inherit_current_file_cleanup_jobs(owners, monkeypatch):
    from services import backup, restore, runtime_state, account_deletion, private_connections
    from services.runtime_paths import get_runtime_paths
    a, b, _ = owners
    actual = account_deletion.shutil.rmtree
    async def exercise():
        with user_scope(b):
            private_connections.save('synthetic', 'before-delete')
        point = await asyncio.to_thread(backup.create_backup)
        await accounts.set_user_active(actor_id=a, user_id=b, active=False)
        def fail(*args, **kwargs):
            raise PermissionError('synthetic')
        monkeypatch.setattr(account_deletion.shutil, 'rmtree', fail)
        assert (await accounts.delete_user(actor_id=a, user_id=b, confirmation_username='owner-b'))['files_pending']
        monkeypatch.setattr(account_deletion.shutil, 'rmtree', actual)
        await restore.restore_backup(point['id'], actor_id=a)
        with runtime_state.runtime_scope():
            assert await account_deletion.pending_cleanups(actor_id=a) == []
            assert any(user.id == b and user.active for user in await accounts.list_users(actor_id=a))
            assert (get_runtime_paths().config / 'users' / str(b) / 'connections.json').exists()
    asyncio.run(exercise())


def test_authenticated_request_cannot_recreate_records_after_deletion(owners):
    import database as db
    a, b, _ = owners
    async def exercise():
        reached, release = asyncio.Event(), asyncio.Event()
        async def delayed_trade():
            reached.set()
            await release.wait()
            await db.add_position_action('600000', 'BUY', 10, 100, user_id=b)
        task = asyncio.create_task(delayed_trade())
        await reached.wait()
        await accounts.set_user_active(actor_id=a, user_id=b, active=False)
        await accounts.delete_user(actor_id=a, user_id=b, confirmation_username='owner-b')
        release.set()
        with pytest.raises(sqlite3.IntegrityError, match='账号已删除'):
            await task
        assert await db.get_position_actions(user_id=b) == []
    asyncio.run(exercise())


def test_background_maintenance_retries_pending_personal_files(owners, monkeypatch):
    from services import account_deletion, maintenance_jobs, backup
    from services.runtime_paths import get_runtime_paths
    a, b, _ = owners
    file = get_runtime_paths().file('media', f'{b}/pending.png')
    file.write_bytes(b'synthetic')
    actual = account_deletion.shutil.rmtree
    def fail(*args, **kwargs):
        raise PermissionError('synthetic')
    async def exercise():
        await accounts.set_user_active(actor_id=a, user_id=b, active=False)
        monkeypatch.setattr(account_deletion.shutil, 'rmtree', fail)
        assert (await accounts.delete_user(actor_id=a, user_id=b, confirmation_username='owner-b'))['files_pending']
        monkeypatch.setattr(account_deletion.shutil, 'rmtree', actual)
        monkeypatch.setattr(backup, 'backup_if_due', lambda **kwargs: None)
        assert await maintenance_jobs.maintenance_once(startup=True)
        assert await account_deletion.pending_cleanups(actor_id=a) == []
        assert not file.parent.exists()
    asyncio.run(exercise())


def test_restore_deleted_snapshot_does_not_merge_revived_users_private_state(owners):
    from services import backup, restore, runtime_state, search, model_catalog, notifications
    import database as db
    a, b, path = owners
    async def exercise():
        before = await asyncio.to_thread(backup.create_backup)
        await accounts.set_user_active(actor_id=a, user_id=b, active=False)
        await accounts.delete_user(actor_id=a, user_id=b, confirmation_username='owner-b')
        after = await asyncio.to_thread(backup.create_backup)
        await restore.restore_backup(before['id'], actor_id=a)
        with runtime_state.runtime_scope(), user_scope(b):
            await search.preference(False)
            await model_catalog.save_preferences(local_only=True, scenes={})
            await db.set_user_setting('notification_legacy_imported', '1', user_id=b)
            await notifications.emit('risk', 'revived', title='合成私人通知')
        with runtime_state.runtime_scope():
            await restore.restore_backup(after['id'], actor_id=a)
        with runtime_state.runtime_scope():
            assert [user.id for user in await accounts.list_users(actor_id=a)] == [a]
            with sqlite3.connect(path) as connection:
                for table in ('llm_preferences', 'search_preferences', 'notifications', 'user_settings'):
                    assert connection.execute(f'SELECT COUNT(*) FROM {table} WHERE user_id=?', (b,)).fetchone()[0] == 0
    asyncio.run(exercise())
