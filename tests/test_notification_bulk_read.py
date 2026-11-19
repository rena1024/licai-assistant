import asyncio

from fastapi.testclient import TestClient

from tests.test_ownership_portfolio import owners
from services.user_context import user_scope


def test_bulk_read_is_atomic_private_and_idempotent(owners):
    from run import app
    from services import notifications as n
    a, b, _ = owners

    async def setup():
        with user_scope(a):
            foreign = await n.emit('risk', 'foreign', title='管理员消息')
        with user_scope(b):
            own = [await n.emit('risk', str(i), title='个人消息') for i in range(3)]
        return foreign, own

    foreign, own = asyncio.run(setup())
    with TestClient(app) as client:
        login = client.post('/api/auth/login', json={'username': 'owner-b', 'password': 'Synthetic-password-02!'})
        client.headers['X-CSRF-Token'] = login.json()['csrf_token']
        for invalid in (foreign, 'missing'):
            assert client.post('/api/notifications/read', json={'ids': [own[0], invalid]}).status_code == 404
            assert all(m['read_at'] is None for m in client.get('/api/notifications').json()['messages'])
        for ids in ([], own * 17):
            assert client.post('/api/notifications/read', json={'ids': ids}).status_code == 422
        for _ in range(2):
            assert client.post('/api/notifications/read', json={'ids': own[:2]}).status_code == 200
        rows = {m['id']: m for m in client.get('/api/notifications').json()['messages']}
        assert rows[own[0]]['read_at'] is not None
        assert rows[own[1]]['read_at'] is not None
        assert rows[own[2]]['read_at'] is None

    async def check_foreign():
        with user_scope(a):
            assert (await n.list_messages())[0]['read_at'] is None
    asyncio.run(check_foreign())
