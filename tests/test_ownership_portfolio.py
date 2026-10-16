"""持仓与资产双用户隔离，使用临时库、合成账号及确定性行情。"""
import asyncio
import sqlite3

import pytest
from fastapi.testclient import TestClient

PASSWORD = 'Synthetic-password-01!'


@pytest.fixture
def owners(tmp_path, monkeypatch):
    monkeypatch.setenv('LICAI_DATA_DIR', str(tmp_path))
    import database as db
    from services import accounts

    async def setup():
        await db.init_db()
        path = await accounts.issue_bootstrap_token()
        admin = await accounts.bootstrap_admin(token=path.read_text().strip(), username='owner-a', password=PASSWORD)
        other = await accounts.create_user(actor_id=admin.id, username='owner-b', password=PASSWORD)
        from services import auth
        session = await auth.login(username=other.username, password=PASSWORD)
        await auth.change_password(session.token, current_password=PASSWORD, new_password='Synthetic-password-02!')
        return admin.id, other.id

    a, b = asyncio.run(setup())
    return a, b, tmp_path / 'portfolio.db'


def test_same_symbol_isolated_and_foreign_id_denied(owners):
    import database as db
    a, b, _ = owners

    async def exercise():
        await db.add_holding('600000', 'A持仓', 100, 10, user_id=a)
        await db.add_holding('600000', 'B持仓', 200, 20, user_id=b)
        aid = await db.add_position_action('600000', 'BUY', 10, 100, user_id=a)
        await db.add_position_action('600000', 'BUY', 20, 200, user_id=b)
        assert (await db.get_holding('600000', user_id=a))['shares'] == 100
        assert (await db.get_holding('600000', user_id=b))['shares'] == 200
        assert len(await db.get_all_holdings(user_id=a)) == 1
        assert [x['id'] for x in await db.get_position_actions(user_id=a)] == [aid]
        assert not await db.update_position_action(aid, price=99, user_id=b)
        assert not await db.delete_position_action(aid, user_id=b)
        assert (await db.get_position_actions(user_id=a))[0]['price'] == 10
        await db.delete_holding('600000', user_id=b)
        assert await db.get_holding('600000', user_id=a)
        assert await db.get_position_actions(user_id=a)

    asyncio.run(exercise())


def test_foreign_assets_actions_merge_and_delete_cannot_cross_owners(owners):
    import database as db
    a, b, _ = owners

    async def exercise():
        aid = await db.add_external_asset('CASH', 'cash', 'A现金', '', 100, user_id=a)
        bid = await db.add_external_asset('CASH', 'cash', 'B现金', '', 200, user_id=b)
        action = await db.add_external_action(aid, 'DEPOSIT', amount=100, user_id=a)
        assert await db.get_external_asset(aid, user_id=b) is None
        assert await db.list_external_actions(aid, user_id=b) == []
        assert not await db.update_external_action(action, amount=9, user_id=b)
        assert not await db.delete_external_action(action, user_id=b)
        with pytest.raises(ValueError):
            await db.add_external_action(aid, 'DEPOSIT', amount=9, user_id=b)
        with pytest.raises(ValueError):
            await db.reassign_external_actions(aid, bid, user_id=b)
        await db.delete_external_asset(aid, user_id=b)
        assert (await db.list_external_actions(aid, user_id=a))[0]['amount'] == 100
        assert (await db.get_external_asset(aid, user_id=a))['cost_amount'] == 100
        assert [x['id'] for x in await db.list_external_assets(user_id=b)] == [bid]

    asyncio.run(exercise())


def test_private_database_requires_explicit_valid_identity(owners):
    import database as db

    async def exercise():
        with pytest.raises(TypeError):
            await db.get_all_holdings()
        for invalid in (None, 0, -1, True, '1'):
            with pytest.raises(ValueError):
                await db.get_all_holdings(user_id=invalid)

    asyncio.run(exercise())


def test_http_cannot_edit_another_users_action_or_asset(owners):
    import database as db
    from run import app
    a, b, _ = owners

    async def seed():
        await db.add_holding('600000', 'A', 100, 10, user_id=a)
        action = await db.add_position_action('600000', 'BUY', 10, 100, user_id=a)
        asset = await db.add_external_asset('CASH', 'cash', 'A现金', '', 100, user_id=a)
        return action, asset

    action, asset = asyncio.run(seed())
    with TestClient(app) as client:
        login = client.post('/api/auth/login', json={'username':'owner-b', 'password':'Synthetic-password-02!'})
        client.headers['X-CSRF-Token'] = login.json()['csrf_token']
        assert client.get('/api/portfolio').json() == []
        assert client.get('/api/portfolio/600000/actions').json() == []
        assert client.put(f'/api/portfolio/actions/{action}', json={'price':99}).status_code == 404
        assert client.delete(f'/api/portfolio/actions/{action}').status_code == 404
        assert client.delete(f'/api/assets/{asset}').status_code == 404
        assert client.get(f'/api/assets/{asset}/actions').status_code == 404


def test_nonempty_unowned_database_is_rejected_without_adoption(owners):
    from database import init_db
    from services.runtime_paths import RuntimePathError
    path = owners[2]
    with sqlite3.connect(path) as connection:
        connection.execute('DROP TABLE holdings')
        connection.execute('CREATE TABLE holdings(id INTEGER PRIMARY KEY, stock_code TEXT UNIQUE, shares INTEGER)')
        connection.execute("INSERT INTO holdings VALUES (1, 'LEGACY', 100)")
    before = path.read_bytes()
    with pytest.raises(RuntimePathError, match='无归属|历史'):
        asyncio.run(init_db())
    assert path.read_bytes() == before


def test_snapshots_and_cached_pnl_do_not_cross_users(owners, monkeypatch):
    import database as db
    from services import today_pnl
    from services.user_context import require_user_id, user_scope
    a, b, _ = owners

    async def compute():
        return {'owner': require_user_id()}

    monkeypatch.setattr(today_pnl, '_compute', compute)

    async def exercise():
        await db.save_portfolio_snapshot('2026-10-16', 100, '{}', user_id=a)
        await db.save_portfolio_snapshot('2026-10-16', 200, '{}', user_id=b)
        assert (await db.get_snapshot_on_or_before('2026-10-16', user_id=a))['total_value'] == 100
        assert (await db.list_portfolio_snapshots(user_id=b))[0]['total_value'] == 200
        with user_scope(a):
            assert (await today_pnl.today_pnl())['owner'] == a
        with user_scope(b):
            assert (await today_pnl.today_pnl())['owner'] == b

    asyncio.run(exercise())


def test_asset_action_must_belong_to_path_asset(owners):
    import database as db
    from run import app
    a, _, _ = owners

    async def seed():
        first = await db.add_external_asset('CASH', 'one', '一', '', 100, user_id=a)
        second = await db.add_external_asset('CASH', 'two', '二', '', 200, user_id=a)
        action = await db.add_external_action(second, 'DEPOSIT', amount=200, user_id=a)
        return first, second, action

    first, second, action = asyncio.run(seed())
    with TestClient(app) as client:
        login = client.post('/api/auth/login', json={'username':'owner-a', 'password':PASSWORD})
        client.headers['X-CSRF-Token'] = login.json()['csrf_token']
        assert client.delete(f'/api/assets/{first}/actions/{action}').status_code == 404
    assert asyncio.run(db.list_external_actions(second, user_id=a))[0]['id'] == action


def test_export_filters_owned_resources_and_import_cannot_replace_them(owners):
    import database as db
    from run import app
    a, b, _ = owners

    async def seed():
        await db.add_holding('600000', 'A私有', 100, 10, user_id=a)
        await db.add_holding('600001', 'B私有', 200, 20, user_id=b)
    asyncio.run(seed())
    with TestClient(app) as client:
        result = client.post('/api/auth/login', json={'username':'owner-b', 'password':'Synthetic-password-02!'})
        client.headers['X-CSRF-Token'] = result.json()['csrf_token']
        exported = client.get('/api/data/export')
        assert [r['stock_code'] for r in exported.json()['tables']['holdings']] == ['600001']
        response = client.post('/api/data/import', files={'file':('owned.json', exported.content, 'application/json')})
        assert response.status_code == 409
    assert asyncio.run(db.get_holding('600000', user_id=a))['shares'] == 100


def test_context_has_no_anonymous_or_default_admin_identity():
    from contextvars import Context
    from services.user_context import require_user_id
    with pytest.raises(ValueError):
        Context().run(require_user_id)


def test_database_foreign_key_rejects_foreign_asset_parent(owners):
    import database as db
    a, b, _ = owners

    async def exercise():
        asset = await db.add_external_asset('CASH', 'cash', 'A', '', 100, user_id=a)
        connection = await db.get_db()
        try:
            with pytest.raises(sqlite3.IntegrityError):
                await connection.execute('INSERT INTO external_asset_actions(user_id,asset_id,action_type,amount) VALUES (?,?,?,?)', (b, asset, 'DEPOSIT', 999))
        finally:
            await connection.close()
        assert await db.list_external_actions(asset, user_id=a) == []
    asyncio.run(exercise())


def test_identity_survives_concurrent_service_calls_and_is_restored(owners):
    from services.user_context import user_scope, require_user_id
    import database as db
    a, b, _ = owners

    async def scoped_read(user_id):
        with user_scope(user_id):
            await asyncio.sleep(0)
            return [row['stock_name'] for row in await db.get_all_holdings(user_id=require_user_id())]

    async def exercise():
        await db.add_holding('600000', 'A', 100, 10, user_id=a)
        await db.add_holding('600000', 'B', 200, 20, user_id=b)
        assert await asyncio.gather(scoped_read(a), scoped_read(b)) == [['A'], ['B']]
    asyncio.run(exercise())


def test_empty_legacy_tables_upgrade_without_changing_accounts(owners):
    import database as db
    path = owners[2]
    with sqlite3.connect(path) as connection:
        before = connection.execute('SELECT * FROM users ORDER BY id').fetchall()
        connection.execute('DROP TABLE holdings')
        connection.execute('CREATE TABLE holdings(id INTEGER PRIMARY KEY, stock_code TEXT UNIQUE, shares INTEGER)')
        connection.execute("DELETE FROM runtime_metadata WHERE key='portfolio_schema_version'")
    asyncio.run(db.init_db())
    with sqlite3.connect(path) as connection:
        assert connection.execute('SELECT * FROM users ORDER BY id').fetchall() == before
        assert 'user_id' in [row[1] for row in connection.execute('PRAGMA table_info(holdings)')]
        assert connection.execute("SELECT value FROM runtime_metadata WHERE key='portfolio_schema_version'").fetchone() == ('1',)
    asyncio.run(db.init_db())


def test_asset_linked_schedules_cannot_be_changed_by_another_owner(owners):
    import database as db
    a, b, _ = owners

    async def exercise():
        asset = await db.add_external_asset('FUND', '123456', 'A基金', '', 100, user_id=a)
        schedule = await db.add_dca_schedule(asset, 'amount', 100, next_due='2026-10-16', user_id=a)
        assert await db.get_dca_schedule(schedule, user_id=b) is None
        assert await db.list_due_dca_schedules('2026-10-16', user_id=b) == []
        await db.update_dca_schedule(schedule, status='paused', user_id=b)
        assert (await db.get_dca_schedule(schedule, user_id=a))['status'] == 'active'
        with pytest.raises(ValueError):
            await db.add_dca_schedule(asset, 'amount', 200, user_id=b)
    asyncio.run(exercise())


def test_http_same_symbol_and_forged_owner_stay_in_session_scope(owners, monkeypatch):
    import database as db
    import api.portfolio_routes as portfolio
    from run import app
    a, b, _ = owners

    async def quotes(codes):
        return {code: {'price': 10.0, 'change_pct': 0.0, 'stock_name': '公共行情名称'} for code in codes}

    async def sector(code):
        return '测试行业'

    async def no_dilution(code, state):
        return state

    monkeypatch.setattr(portfolio, 'get_realtime_quotes', quotes)
    monkeypatch.setattr(portfolio, 'get_stock_sector', sector)
    monkeypatch.setattr('services.dividends.dilute_state', no_dilution)
    with TestClient(app) as first, TestClient(app) as second:
        for client, username, password in [(first, 'owner-a', PASSWORD), (second, 'owner-b', 'Synthetic-password-02!')]:
            login = client.post('/api/auth/login', json={'username':username, 'password':password})
            client.headers['X-CSRF-Token'] = login.json()['csrf_token']
        for client, shares, name, forged in [(first, 100, 'A私有名称', b), (second, 200, 'B私有名称', a)]:
            result = client.post('/api/portfolio', json={'stock_code':'600000', 'stock_name':name, 'shares':shares, 'cost_price':10, 'user_id':forged})
            assert result.status_code == 200
            holdings = client.get('/api/portfolio').json()
            assert len(holdings) == 1
            assert holdings[0]['stock_name'] == name and holdings[0]['shares'] == shares
        assert asyncio.run(db.get_holding('600000', user_id=a))['stock_name'] == 'A私有名称'
        assert asyncio.run(db.get_holding('600000', user_id=b))['stock_name'] == 'B私有名称'


def test_asset_http_foreign_lot_close_and_merge_are_rejected(owners):
    import database as db
    from run import app
    a, b, _ = owners

    async def seed():
        first = await db.add_external_asset('CASH', 'cash', 'A现金', '', 100, user_id=a)
        second = await db.add_external_asset('CASH', 'cash', 'B现金', '', 200, user_id=b)
        return first, second

    foreign, own = asyncio.run(seed())
    with TestClient(app) as client:
        login = client.post('/api/auth/login', json={'username':'owner-b', 'password':'Synthetic-password-02!'})
        client.headers['X-CSRF-Token'] = login.json()['csrf_token']
        assert [row['id'] for row in client.get('/api/assets').json()['assets']] == [own]
        assert client.put(f'/api/assets/{foreign}', json={'name':'篡改'}).status_code == 404
        assert client.post(f'/api/assets/{foreign}/close').status_code == 404
        assert client.post(f'/api/assets/{own}/merge-from', json={'source_id':foreign}).status_code == 404
    assert asyncio.run(db.get_external_asset(foreign, user_id=a))['name'] == 'A现金'


def test_portfolio_news_cache_does_not_reveal_previous_owners_holdings(owners, monkeypatch):
    import database as db
    import api.news_routes as news
    from services.user_context import user_scope
    a, b, _ = owners
    monkeypatch.setattr(news, '_cache', {})
    monkeypatch.setattr(news, '_fetch_holding_news_sync', lambda code: [{'code':code, 'title':'公共新闻', 'time':'2026-10-16'}])
    monkeypatch.setattr(news, '_fetch_all_notices_sync', lambda: {})

    async def exercise():
        await db.add_holding('600000', 'A私有名称', 100, 10, user_id=a)
        await db.add_holding('600001', 'B私有名称', 100, 10, user_id=b)
        with user_scope(a):
            first = await news.portfolio_news()
            assert first['tracked_codes'] == ['600000']
        with user_scope(b):
            second = await news.portfolio_news()
            assert second['tracked_codes'] == ['600001']
            assert second['items'][0]['name'] == 'B私有名称'
        assert first['items'][0]['name'] == 'A私有名称'
    asyncio.run(exercise())


def test_same_symbol_briefing_does_not_share_cost_or_payload(owners):
    import json
    import database as db
    from run import app
    from services.user_context import user_scope
    a, b, _ = owners

    async def exercise():
        for owner, cost in [(a, 10), (b, 20)]:
            await db.add_holding('600000', f'owner-{owner}', 100, cost, user_id=owner)
            with user_scope(owner):
                await db.save_briefing('600000', '2026-10-16', json.dumps({'stock_code':'600000', 'cost_price':cost}), user_id=owner)
    asyncio.run(exercise())
    with TestClient(app) as client:
        client.post('/api/auth/login', json={'username':'owner-a', 'password':PASSWORD})
        response = client.get('/api/briefing')
        assert [row['cost_price'] for row in response.json()['briefings']] == [10]


def test_legacy_config_import_cannot_clear_private_asset_state(owners):
    import json
    import database as db
    from services import dividends
    from run import app
    a, b, _ = owners
    key = f'{dividends.DISMISS_KEY}:{a}'
    asyncio.run(db.set_config(key, '["US.TEST|2026-10-16"]'))
    payload = json.dumps({'_meta':{'version':1}, 'tables':{'app_config':[]}})
    with TestClient(app) as client:
        login = client.post('/api/auth/login', json={'username':'owner-b', 'password':'Synthetic-password-02!'})
        client.headers['X-CSRF-Token'] = login.json()['csrf_token']
        response = client.post('/api/data/import', files={'file':('legacy.json',payload,'application/json')})
        assert response.status_code == 409
        exported = client.get('/api/data/export').json()
        assert 'app_config' not in exported['tables']
    assert asyncio.run(db.get_config(key)) == '["US.TEST|2026-10-16"]'
