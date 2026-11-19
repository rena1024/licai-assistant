import asyncio
import sqlite3

from fastapi.testclient import TestClient

from tests.test_ownership_portfolio import owners
from services.user_context import user_scope


def test_delete_selected_is_private_atomic_and_keeps_dedup(owners):
    from run import app
    from services import notifications as n, notification_delivery as d
    a, b, path = owners

    async def setup():
        with user_scope(a):
            foreign = await n.emit('risk', 'foreign', title='其他用户通知')
        with user_scope(b):
            channel = await d.save_channel({'kind': 'pushplus', 'name': '合成渠道', 'config': {'token': 'fake'}}, expected_version=0)
            await d.activate(channel['id'])
            own = [await n.emit('risk', str(i), title='个人通知') for i in range(3)]
            await d.activate(None)
        return foreign, own

    foreign, own = asyncio.run(setup())
    with TestClient(app) as client:
        login = client.post('/api/auth/login', json={'username': 'owner-b', 'password': 'Synthetic-password-02!'})
        client.headers['X-CSRF-Token'] = login.json()['csrf_token']
        for invalid in (foreign, 'missing'):
            assert client.post('/api/notifications/delete', json={'ids': [own[0], invalid]}).status_code == 404
            assert len(client.get('/api/notifications').json()['messages']) == 3
        for ids in ([], own * 17):
            assert client.post('/api/notifications/delete', json={'ids': ids}).status_code == 422
        assert client.post('/api/notifications/delete', json={'ids': own[:2]}).status_code == 200
        assert [m['id'] for m in client.get('/api/notifications').json()['messages']] == own[2:]
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT COUNT(*) FROM notification_deliveries WHERE notification_id IN (?,?)', own[:2]).fetchone()[0] == 0
    async def check():
        with user_scope(b):
            assert await n.emit('risk', '0', title='不应重新生成') == own[0]
            assert len(await n.list_messages()) == 1
        with user_scope(a):
            assert (await n.list_messages())[0]['id'] == foreign
    asyncio.run(check())


def test_deletion_does_not_race_an_inflight_delivery(owners):
    from services import notifications as n, notification_delivery as d
    _, b, path = owners
    async def setup():
        with user_scope(b):
            channel = await d.save_channel({'kind': 'pushplus', 'name': '合成渠道', 'config': {'token': 'fake'}}, expected_version=0)
            await d.activate(channel['id'])
            return await n.emit('risk', 'busy', title='发送中通知')
    identifier = asyncio.run(setup())
    from run import app
    with TestClient(app) as client:
        login = client.post('/api/auth/login', json={'username': 'owner-b', 'password': 'Synthetic-password-02!'})
        client.headers['X-CSRF-Token'] = login.json()['csrf_token']
        with sqlite3.connect(path) as db:
            db.execute("UPDATE notification_deliveries SET state='sending'")
        assert client.post('/api/notifications/delete', json={'ids': [identifier]}).status_code == 409
        assert len(client.get('/api/notifications').json()['messages']) == 1
