import asyncio
import json
from fastapi.testclient import TestClient
from tests.test_ownership_portfolio import owners


def test_import_cannot_set_owner_or_replace_auth(owners):
    from run import app
    import database as db
    a,b,_=owners
    asyncio.run(db.add_holding('600000','A',100,10,user_id=a))
    with TestClient(app) as client:
        login=client.post('/api/auth/login',json={'username':'owner-b','password':'Synthetic-password-02!'})
        client.headers['X-CSRF-Token']=login.json()['csrf_token']
        for tables in ({'sessions':[]},{'holdings':[{'user_id':a,'stock_code':'600000','shares':999,'cost_price':1}]}):
            result=client.post('/api/data/import',files={'file':('test.json',json.dumps({'tables':tables}),'application/json')})
            assert result.status_code==400
        result=client.post('/api/data/import',files={'file':('test.json',json.dumps({'tables':{'holdings':[{'stock_code':'600000','stock_name':'B','shares':200,'cost_price':20}]}}),'application/json')})
        assert result.status_code==200
        assert client.get('/api/data/export').json()['tables']['holdings'][0]['stock_name']=='B'
        assert client.post('/api/settings/proxy',json={'proxy':''}).status_code==403
        assert client.get('/api/settings/llm').status_code==403
    assert asyncio.run(db.get_holding('600000',user_id=a))['shares']==100
    assert asyncio.run(db.get_holding('600000',user_id=b))['shares']==200


def test_personal_replace_keeps_internal_job_control_state(owners):
    from run import app
    import database as db
    a,b,_=owners
    async def seed():
        await db.set_user_setting('task_done:eod','2026-10-16',user_id=b)
        await db.set_user_setting('automatic_ai_enabled','0',user_id=b)
        await db.set_user_setting('max_daily_loss','10',user_id=b)
    asyncio.run(seed())
    with TestClient(app) as client:
        result=client.post('/api/auth/login',json={'username':'owner-b','password':'Synthetic-password-02!'})
        client.headers['X-CSRF-Token']=result.json()['csrf_token']
        exported=client.get('/api/data/export')
        assert b'task_done' not in exported.content
        result=client.post('/api/data/import',files={'file':('personal.json',exported.content,'application/json')})
        assert result.status_code==200
    assert asyncio.run(db.get_user_setting('task_done:eod',user_id=b))=='2026-10-16'
    assert asyncio.run(db.get_user_setting('automatic_ai_enabled',user_id=b))=='0'


def test_admin_user_management_and_route_access_matrix(owners):
    from run import app
    from fastapi.routing import APIRoute
    import re
    with TestClient(app) as anonymous:
        for route in app.routes:
            if isinstance(route,APIRoute) and route.path.startswith('/api/') and not route.path.startswith('/api/auth/') and route.path not in {'/api/app-version','/api/health'}:
                path=re.sub(r'\{[^}]+\}','1',route.path)
                method=next(iter(route.methods))
                assert anonymous.request(method,path).status_code==401,(method,path)
    with TestClient(app) as client:
        result=client.post('/api/auth/login',json={'username':'owner-a','password':'Synthetic-password-01!'})
        client.headers['X-CSRF-Token']=result.json()['csrf_token']
        created=client.post('/api/admin/users',json={'username':'owner-c','password':'temporary'})
        assert created.status_code==200
        uid=created.json()['id']
        assert 'password_hash' not in created.json() and 'temporary' not in created.text
        assert client.put(f'/api/admin/users/{uid}/active',json={'active':False}).json()['active'] is False
        assert client.post(f'/api/admin/users/{uid}/password',json={'password':'reset-password'}).status_code==200
        result=client.post('/api/auth/login',json={'username':'owner-b','password':'Synthetic-password-02!'})
        client.headers['X-CSRF-Token']=result.json()['csrf_token']
        assert client.get('/api/admin/users').status_code==403
        assert client.put(f'/api/admin/users/{uid}/active',json={'active':True}).status_code==403
