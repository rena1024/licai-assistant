import asyncio
import pytest
from fastapi.testclient import TestClient
from tests.test_ownership_portfolio import owners


def test_unmigrated_ai_is_closed_but_manual_business_and_health_remain_available(owners):
    from run import app
    from services import ai_gate
    with pytest.raises(ai_gate.AIUnavailable):ai_gate.require_controlled_ai()
    with TestClient(app) as client:
        assert client.get('/api/health').status_code==200
        login=client.post('/api/auth/login',json={'username':'owner-a','password':'Synthetic-password-01!'})
        client.headers['X-CSRF-Token']=login.json()['csrf_token']
        for endpoint in ('/api/news/digest','/api/news/daily-review','/api/market/sentiment-ai','/api/portfolio/trade-review-ai'):
            assert client.get(endpoint).status_code==503
        assert client.post('/api/ask/runs',json={'question':'synthetic'}).status_code==503
        assert client.get('/api/portfolio').status_code==200


def test_backup_management_never_exposes_other_users_content(owners):
    from run import app
    with TestClient(app) as client:
        login=client.post('/api/auth/login',json={'username':'owner-b','password':'Synthetic-password-02!'})
        client.headers['X-CSRF-Token']=login.json()['csrf_token']
        assert client.get('/api/admin/backups').status_code==403
        login=client.post('/api/auth/login',json={'username':'owner-a','password':'Synthetic-password-01!'})
        client.headers['X-CSRF-Token']=login.json()['csrf_token']
        created=client.post('/api/admin/backups')
        assert created.status_code==200
        assert set(created.json())=={'id','kind','created_at','file_count'}
        assert client.get('/api/admin/backups').status_code==200
        assert client.get('/api/admin/operations').status_code==200
        assert client.post('/api/admin/trace',json={'enabled':True}).status_code==200
        assert client.get('/api/admin/operations').json()['trace']['enabled']
