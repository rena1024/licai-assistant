"""私人来源不能复用其他账号的凭证和缓存。"""
from tests.test_ownership_portfolio import owners
from services.user_context import user_scope


def test_two_credentials_do_not_reuse_client_or_cache(owners):
    from services import okx_client as okx, zsxq_client as zx
    a,b,_=owners
    with user_scope(a):
        assert okx.save_credentials('synthetic-a','secret-a','pass-a')[0]
        zx.configure(url='https://example.invalid/mcp?api_key=synthetic-a',groups=[{'group_id':'1'}])
    with user_scope(b):
        assert not okx.has_credentials()
        assert not zx.is_enabled()
        assert okx.save_credentials('synthetic-b','secret-b','pass-b')[0]
        zx.configure(url='https://example.invalid/mcp?api_key=synthetic-b',groups=[{'group_id':'2'}])
        assert zx.configured_groups()==[{'group_id':'2'}]
        assert 'synthetic' not in zx.endpoint_label()
    with user_scope(a):
        assert okx.load_credentials()['api_key']=='synthetic-a'
        assert zx.configured_groups()==[{'group_id':'1'}]
        okx.clear_credentials()
    with user_scope(b):
        assert okx.load_credentials()['api_key']=='synthetic-b'


def test_private_provider_history_does_not_enter_shared_pool(owners,monkeypatch):
    import asyncio
    from services import limit_up_pool as pool
    from services.providers import gateway
    import database as db
    a,b,_=owners
    monkeypatch.setattr(pool,'_fetch_em_sync',lambda day: [])
    async def private(*args):
        return [{'snap_date':'2026-10-16','stock_code':'600000','name':'私人来源','source':'provider'}]
    monkeypatch.setattr(gateway,'try_call',private)
    async def exercise():
        with user_scope(a):
            await pool.sync_day('2026-10-16',allow_provider=True)
            assert (await db.get_limit_up_pool('2026-10-16',user_id=a))[0]['name']=='私人来源'
        assert await db.get_limit_up_pool('2026-10-16',user_id=b)==[]
        assert await db.get_limit_up_pool('2026-10-16')==[]
    asyncio.run(exercise())


def test_provider_code_removal_is_admin_only_but_personal_clear_is_allowed(owners):
    import asyncio
    from fastapi.testclient import TestClient
    from run import app
    import database as db
    asyncio.run(db.set_config('market_provider','missing.module:Provider'))
    with TestClient(app) as client:
        result=client.post('/api/auth/login',json={'username':'owner-b','password':'Synthetic-password-02!'})
        client.headers['X-CSRF-Token']=result.json()['csrf_token']
        assert client.delete('/api/settings/provider').status_code==200
        assert asyncio.run(db.get_config('market_provider'))=='missing.module:Provider'
        assert client.post('/api/settings/provider',json={'spec':''}).status_code==403
        result=client.post('/api/auth/login',json={'username':'owner-a','password':'Synthetic-password-01!'})
        client.headers['X-CSRF-Token']=result.json()['csrf_token']
        assert client.delete('/api/settings/provider').status_code==200
        assert asyncio.run(db.get_config('market_provider'))==''
