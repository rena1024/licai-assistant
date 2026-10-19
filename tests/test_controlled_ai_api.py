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
