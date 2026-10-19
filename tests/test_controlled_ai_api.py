import asyncio
from fastapi.testclient import TestClient
from tests.test_ownership_portfolio import owners
from tests.test_llm_attempts import configure


def login(client,name,password):
    result=client.post('/api/auth/login',json={'username':name,'password':password})
    assert result.status_code==200
    client.headers['X-CSRF-Token']=result.json()['csrf_token']


def test_model_and_budget_routes_enforce_real_sessions_and_secret_redaction(owners):
    from run import app
    a,b,_=owners
    with TestClient(app) as client:
        login(client,'owner-b','Synthetic-password-02!')
        assert client.put('/api/models',json={}).status_code==403
        assert client.put('/api/usage/limits',json={'subject':b,'day':'1','month':'1'}).status_code==403
        assert client.get(f'/api/usage?subject={a}').status_code==403
        assert client.post('/api/models/m/test',json={'capability':'text'}).status_code==403
        assert set(client.get('/api/usage').json()['budget'])=={'user'}
        login(client,'owner-a','Synthetic-password-01!')
        invalid=client.put('/api/models',json={'id':'m','api_key':'do-not-leak-synthetic','expected_version':0})
        assert invalid.status_code==422 and 'do-not-leak' not in invalid.text
        response=client.put('/api/models',json={'id':'m','protocol':'ollama','endpoint':'http://localhost:11434','model':'synthetic','local':True,'expected_version':0})
        assert response.status_code==200 and 'api_key' not in response.json()
        assert client.get('/api/usage').status_code==200
        assert client.get('/api/models/preferences').status_code==200


def test_diagnostic_uses_global_budget_and_persists_capability_result(owners,monkeypatch):
    from run import app
    from services import llm_runtime
    a,b,_=owners
    asyncio.run(configure(a,b))
    seen=[]
    def fake(model,payload,path,headers):
        seen.append(payload)
        return {'content':[{'type':'text','text':'OK'}],'usage':{'input_tokens':1,'output_tokens':1}}
    monkeypatch.setattr(llm_runtime,'send_http',fake)
    with TestClient(app) as client:
        login(client,'owner-a','Synthetic-password-01!')
        result=client.post('/api/models/cloud/test',json={'capability':'text'})
        assert result.status_code==200
        checks=client.get('/api/models').json()['models'][0]['checks']
        assert checks[0]['success']==1
        global_usage=client.get('/api/usage?subject=0').json()
        assert global_usage['details'][0]['purpose']=='diagnostic'
        login(client,'owner-b','Synthetic-password-02!')
        assert client.get('/api/usage').json()['details']==[]
    assert len(seen)==1


def test_saved_ollama_model_is_readable_after_relogin_and_preference_selection(owners):
    """使用前端表单的完整请求，验证写库、列表及新会话，而非伪造保存响应。"""
    from run import app
    payload = dict(id='local_ollama', protocol='ollama', endpoint='http://localhost:11434',
                   model='qwen3:8b', local=True, enabled=True, api_key='',
                   capabilities=['text'], context_limit=32768, price=None, expected_version=0)
    with TestClient(app) as client:
        login(client, 'owner-a', 'Synthetic-password-01!')
        saved = client.put('/api/models', json=payload)
        assert saved.status_code == 200
        listed = client.get('/api/models').json()['models']
        assert [(m['id'], m['model'], m['version']) for m in listed] == [('local_ollama', 'qwen3:8b', 1)]
        assert client.get('/api/models/preferences').json()['scenes'] == {}
        prefs = client.put('/api/models/preferences', json=dict(
            local_only=True, scenes={'ask': ['local_ollama']}, expected_version=0))
        assert prefs.status_code == 200
    with TestClient(app) as client:
        login(client, 'owner-a', 'Synthetic-password-01!')
        assert client.get('/api/models').json()['models'][0]['id'] == 'local_ollama'
        assert client.get('/api/models/preferences').json()['scenes']['ask'] == ['local_ollama']


def test_policy_is_admin_only_and_persists_local_concurrency(owners):
    from run import app
    a,b,_=owners
    with TestClient(app) as client:
        login(client,'owner-b','Synthetic-password-02!')
        assert client.get('/api/usage/policy').status_code==403
        login(client,'owner-a','Synthetic-password-01!')
        result=client.put('/api/usage/policy',json={'detail_days':7,'warning_threshold':75,'local_concurrency':2})
        assert result.status_code==200
        assert client.get('/api/usage/policy').json()=={'detail_days':7,'warning_threshold':75,'local_concurrency':2}


def test_model_lifecycle_requires_admin_and_preserves_choices_and_history(owners):
    from run import app
    from services import model_catalog as catalog
    from services.llm_types import LLMError
    from services.user_context import user_scope
    import sqlite3
    import pytest
    a, b, path = owners
    payload = dict(id='local-life', protocol='ollama', endpoint='http://localhost:11434',
                   model='qwen3:8b', local=True, enabled=True, expected_version=0)
    with TestClient(app) as client:
        login(client, 'owner-a', 'Synthetic-password-01!')
        assert client.put('/api/models', json=payload).status_code == 200
        assert client.request('DELETE', '/api/models/local-life', json={'expected_version': 1}).status_code == 409
        login(client, 'owner-b', 'Synthetic-password-02!')
        assert client.put('/api/models/preferences', json=dict(local_only=True, scenes={'ask': ['local-life']}, expected_version=0)).status_code == 200
        assert client.patch('/api/models/local-life', json=dict(enabled=False, expected_version=1)).status_code == 403
        assert client.request('DELETE', '/api/models/local-life', json={'expected_version': 1}).status_code == 403
        login(client, 'owner-a', 'Synthetic-password-01!')
        disabled = client.patch('/api/models/local-life', json=dict(enabled=False, expected_version=1))
        assert disabled.status_code == 200 and disabled.json()['version'] == 2
        assert disabled.json()['enabled'] is False
        assert client.patch('/api/models/local-life', json=dict(enabled=True, expected_version=1)).status_code == 409
        enabled = client.patch('/api/models/local-life', json=dict(enabled=True, expected_version=2))
        assert enabled.status_code == 200 and enabled.json()['enabled'] is True
        assert client.patch('/api/models/local-life', json=dict(enabled=False, expected_version=3)).status_code == 200
        with sqlite3.connect(path) as db:
            db.execute("INSERT INTO llm_model_checks VALUES ('local-life',1,'text',1,'',1)")
            db.execute("INSERT INTO llm_usage_monthly VALUES (?,'2026-10','local-life','ask',1,1,1)", (a,))
        assert client.request('DELETE', '/api/models/local-life', json={'expected_version': 3}).status_code == 409
        assert client.request('DELETE', '/api/models/local-life', json={'expected_version': 4}).status_code == 200
        assert client.get('/api/models').json()['models'] == []
        # 禁止复用已删除标识，避免旧个人选择或排队任务绑定到另一项新服务。
        assert client.put('/api/models', json=payload).status_code == 409
        login(client, 'owner-b', 'Synthetic-password-02!')
        assert client.get('/api/models/preferences').json()['scenes']['ask'] == ['local-life']
        with user_scope(b):
            assert asyncio.run(catalog.candidates('ask', {'text'})) == []
            with pytest.raises(LLMError):
                asyncio.run(catalog.private_model('local-life'))
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT COUNT(*) FROM llm_model_checks WHERE model_id='local-life'").fetchone()[0] == 1
            assert db.execute("SELECT COUNT(*) FROM llm_usage_monthly WHERE model_id='local-life'").fetchone()[0] == 1
            assert 'api_key' not in db.execute("SELECT config FROM llm_models WHERE id='local-life'").fetchone()[0]


def test_disabled_cloud_model_cannot_be_enabled_without_complete_configuration(owners):
    from run import app
    with TestClient(app) as client:
        login(client, 'owner-a', 'Synthetic-password-01!')
        assert client.put('/api/models', json=dict(id='incomplete', protocol='deepseek',
            endpoint='https://api.deepseek.com', model='synthetic', enabled=False, expected_version=0)).status_code == 200
        assert client.patch('/api/models/incomplete', json=dict(enabled=True, expected_version=1)).status_code == 422
        assert client.patch('/api/models/incomplete', json=dict(enabled='false', expected_version=1)).status_code == 422
        model = client.get('/api/models').json()['models'][0]
        assert model['enabled'] is False and model['version'] == 1


def test_deleted_model_preserves_unknown_reservation_and_late_reconciliation(owners):
    from services import model_catalog as catalog, budget, usage
    from services.user_context import user_scope
    from services.llm_types import CallContext, Usage, LLMError
    from tests.test_budget_ledger import PRICE
    import pytest
    a, b, _ = owners

    async def exercise():
        await configure(a, b)
        with user_scope(b):
            reservation = await budget.reserve_attempt(CallContext(b, 'ask', 'late-delete'), 'cloud', PRICE, 1000, 'late-delete')
            await budget.settle_attempt(reservation, None)
            before = await budget.summary(subject=b)
        with user_scope(a):
            await catalog.set_model_enabled('cloud', enabled=False, expected_version=1)
            await catalog.delete_model('cloud', expected_version=2)
            await catalog.save_model(dict(id='cloud-new', protocol='anthropic', endpoint='https://api.anthropic.com',
                model='synthetic', api_key='synthetic', price=PRICE), expected_version=0)
        with user_scope(b):
            assert await budget.summary(subject=b) == before
            assert (await usage.report())['pending'][0]['state'] == 'unknown'
            # 删除配置不能通过换模型标识绕开同一服务端点的未知费用限制。
            with pytest.raises(LLMError) as caught:
                await budget.reserve_attempt(CallContext(b, 'ask', 'new-delete'), 'cloud-new', PRICE, 1000, 'new-delete')
            assert caught.value.code == 'usage_unknown'
        with user_scope(a):
            await usage.reconcile(reservation, Usage(input_tokens=10, output_tokens=1), '删除服务后核对迟到用量')
        with user_scope(b):
            report = await usage.report()
            assert report['pending'] == []
            assert report['details'][0]['state'] == 'settled'
            assert report['details'][0]['model_id'] == 'cloud'
            assert report['budget']['user']['day']['reserved'] == 0
            assert report['budget']['user']['day']['spent'] == budget.cost_micro(PRICE, Usage(input_tokens=10, output_tokens=1))
    asyncio.run(exercise())
