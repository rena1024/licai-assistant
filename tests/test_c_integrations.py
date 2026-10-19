import asyncio
import sqlite3
import pytest
from fastapi.testclient import TestClient
from tests.test_ownership_portfolio import owners,PASSWORD
from services.user_context import user_scope


def test_budget_events_are_routed_and_deduplicated(owners):
    from services import budget,notifications
    from services.llm_types import CallContext
    from tests.test_budget_ledger import PRICE
    a,b,_=owners
    async def exercise():
        with user_scope(a):
            await budget.set_limits(0,day='0.000100',month='1')
            await budget.set_limits(b,day='0.000100',month='1')
        with user_scope(b):
            await budget.reserve_attempt(CallContext(b,'ask','task'),'cloud',PRICE,80,'event')
            assert any(m['event']=='personal_budget' for m in await notifications.list_messages())
        with user_scope(a):
            assert any(m['event']=='global_budget' for m in await notifications.list_messages())
    asyncio.run(exercise())


def test_source_failure_recovery_only_once(owners):
    from services import data_sources as ds,notifications as n
    a,b,_=owners
    async def exercise():
        for success in (False,False,True,True):
            await ds.record('quote','sina','A',success,now=1792375200)
        with user_scope(a):
            assert sorted(m['event'] for m in await n.list_messages())==['source_failed','source_recovered']
        with user_scope(b):
            assert await n.list_messages()==[]
    asyncio.run(exercise())


def test_api_private_resources_and_public_admin_settings(owners):
    from run import app
    a,b,_=owners
    with TestClient(app) as client:
        login=client.post('/api/auth/login',json={'username':'owner-b','password':'Synthetic-password-02!'})
        client.headers['X-CSRF-Token']=login.json()['csrf_token']
        assert client.get('/api/settings/data-center').status_code==403
        assert client.get('/api/search/channels').status_code==403
        assert client.get('/api/notifications/channels').status_code==200
        assert client.post('/api/notifications/restore-confirmation').status_code==403
        c=client.put('/api/notifications/channels',json={'kind':'pushplus','name':'B微信','config':{'token':'synthetic-private-token'},'expected_version':0})
        assert c.status_code==200
        cid=c.json()['id']
        assert 'synthetic-private-token' not in client.get('/api/notifications/channels').text
        login=client.post('/api/auth/login',json={'username':'owner-a','password':PASSWORD})
        client.headers['X-CSRF-Token']=login.json()['csrf_token']
        assert client.get('/api/notifications/channels').json()['channels']==[]
        assert client.put('/api/notifications/active',json={'channel_id':cid}).status_code==409
        assert client.get('/api/settings/data-center').status_code==200


def test_restore_keeps_search_consumption_and_interrupts_notifications(owners,monkeypatch):
    from services import backup,search,notification_delivery as d,notifications as n
    from tests.test_search_quota import config
    from services.c_recovery import preserve_data_controls
    a,b,path=owners
    point=backup.create_backup()
    async def fake(*args): return []
    monkeypatch.setattr(search,'send',fake)
    async def exercise():
        with user_scope(a):
            await search.save_channel(config(),expected_version=0)
        with user_scope(b):
            await search.search('市场',now=1792375200)
            c=await d.save_channel({'kind':'pushplus','name':'微信','config':{'token':'fake'}},expected_version=0)
            await d.activate(c['id'])
            await n.emit('risk','after-backup',title='风险')
    asyncio.run(exercise())
    import shutil
    target=path.parent/'c-restored.db'
    shutil.copyfile(backup.validate_backup(point['id'])/'database.sqlite3',target)
    with sqlite3.connect(path) as current,sqlite3.connect(target) as restored:
        preserve_data_controls(current,restored)
        assert restored.execute('SELECT sum(used) FROM search_credits').fetchone()[0]==1
        assert restored.execute('SELECT state FROM notification_deliveries').fetchone()[0]=='interrupted'


def test_personal_risk_uses_complete_fresh_prices_and_routes_only_to_owner(owners,monkeypatch):
    from services import notifications as n
    import api.portfolio_routes as pr
    a,b,_=owners
    async def holdings():
        return [{'shares':100,'current_price':9,'price_change_pct':-10,'fx_rate':1,'quote_quality':'fresh','market_state':'open','stock_code':'600000'}]
    monkeypatch.setattr(pr,'list_holdings',holdings)
    async def exercise():
        import database
        with user_scope(b):
            await database.set_config('max_daily_loss','50')
            await n.check_personal_risk()
            await n.check_personal_risk()
            assert len(await n.list_messages())==1
        with user_scope(a): assert await n.list_messages()==[]
    asyncio.run(exercise())


def test_restore_preserves_disabled_search_preference_by_uuid(owners):
    from services import backup,search
    from services.c_recovery import preserve_data_controls
    import shutil
    a,b,path=owners
    point=backup.create_backup()
    async def change():
        with user_scope(b):await search.preference(False)
    asyncio.run(change())
    target=path.parent/'restore-search-off.db'
    shutil.copyfile(backup.validate_backup(point['id'])/'database.sqlite3',target)
    with sqlite3.connect(path) as current,sqlite3.connect(target) as restored:
        preserve_data_controls(current,restored)
        assert restored.execute('SELECT enabled FROM search_preferences WHERE user_id=?',(b,)).fetchone()[0]==0
