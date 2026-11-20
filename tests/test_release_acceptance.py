"""双用户完整离线旅程；外部模型、搜索和通知均不连接真实服务。"""
import asyncio

from fastapi.testclient import TestClient
from tests.test_ownership_portfolio import owners, PASSWORD
from services.user_context import user_scope


def test_two_user_full_journey(owners, monkeypatch):
    from run import app
    import database
    from services import backup, restore, runtime_state, model_catalog, budget, notifications, llm_runtime
    from services.llm_types import CallContext, ModelRequest
    from tests.test_budget_ledger import PRICE
    admin, ordinary, _ = owners

    async def prepare():
        with user_scope(admin):
            await database.add_holding('600000', 'Synthetic admin asset', 100, 10, user_id=admin)
            await model_catalog.save_model(dict(id='synthetic-local', protocol='ollama', local=True,
                endpoint='http://localhost:11434', model='synthetic'), expected_version=0)
            await model_catalog.save_model(dict(id='synthetic-cloud', protocol='deepseek',
                endpoint='https://api.deepseek.com', model='synthetic', api_key='synthetic', price=PRICE), expected_version=0)
            await budget.set_limits(0, day='10', month='10')
            await budget.set_limits(ordinary, day='10', month='10')
        with user_scope(ordinary):
            await database.add_holding('600001', 'Synthetic ordinary asset', 200, 20, user_id=ordinary)
            await model_catalog.save_preferences(local_only=True, scenes={'market': ['synthetic-cloud', 'synthetic-local']})
            await notifications.emit('risk', 'journey', title='Synthetic ordinary notice')
    asyncio.run(prepare())
    sent = []
    def synthetic_send(model, *args):
        assert model.local, '仅本地用户不得回退到云端'
        sent.append(model.id)
        return {'message': {'role': 'assistant', 'content': 'Synthetic answer'}, 'done': True,
                'prompt_eval_count': 1, 'eval_count': 1}
    monkeypatch.setattr(llm_runtime, 'send_http', synthetic_send)
    with TestClient(app) as a, TestClient(app) as b:
        for client, username, password in ((a, 'owner-a', PASSWORD), (b, 'owner-b', 'Synthetic-password-02!')):
            login = client.post('/api/auth/login', json={'username': username, 'password': password})
            assert login.status_code == 200
            client.headers['X-CSRF-Token'] = login.json()['csrf_token']
        assert b.get('/api/admin/users').status_code == 403
        assert a.get('/api/notifications').json()['messages'] == []
        assert any(message['title'] == 'Synthetic ordinary notice' for message in b.get('/api/notifications').json()['messages'])
        assert b.put('/api/search/preferences', json={'enabled': False}).status_code == 200
        assert b.get('/api/search/preferences').json()['enabled'] is False
        assert a.get('/api/search/preferences').json()['enabled'] is True
        async def infer():
            with user_scope(ordinary):
                return await llm_runtime.generate(CallContext(ordinary, 'market', 'journey'), ModelRequest(messages=[]))
        assert asyncio.run(infer()).model_id == 'synthetic-local'
        assert sent == ['synthetic-local']
        assert b.get('/api/usage').json()['details'][0]['cost'] == 0
        assert a.get('/api/usage').json()['details'] == []
        point = a.post('/api/admin/backups')
        assert point.status_code == 200
        asyncio.run(database.update_holding('600001', shares=300, user_id=ordinary))
        asyncio.run(restore.restore_backup(point.json()['id'], actor_id=admin))
        assert a.get('/api/auth/me').status_code == 401
        assert b.get('/api/auth/me').status_code == 401
        async def verify_restored():
            with runtime_state.runtime_scope():
                assert (await database.get_holding('600001', user_id=ordinary))['shares'] == 200
                assert await database.get_holding('600001', user_id=admin) is None
                with user_scope(ordinary):
                    assert (await model_catalog.preferences())['local_only'] is True
        asyncio.run(verify_restored())
        assert runtime_state.read_state()['cloud_hold'] is True
        assert runtime_state.read_state()['notification_hold'] is True
        login = b.post('/api/auth/login', json={'username': 'owner-b', 'password': 'Synthetic-password-02!'})
        assert login.status_code == 200
        assert b.get('/api/search/preferences').json()['enabled'] is False
