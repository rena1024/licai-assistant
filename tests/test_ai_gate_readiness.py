import asyncio

from fastapi.testclient import TestClient

from tests.test_ownership_portfolio import owners
from services.user_context import user_scope


def test_text_only_ask_model_explains_required_tools_at_all_entrypoints(owners):
    from run import app
    from services import model_catalog
    admin, _, _ = owners

    async def setup():
        with user_scope(admin):
            await model_catalog.save_model(dict(
                id='text-only', protocol='ollama', endpoint='http://localhost:11434',
                model='synthetic', local=True, capabilities=['text']), expected_version=0)
            await model_catalog.save_preferences(local_only=True, scenes={'ask': ['text-only']})

    asyncio.run(setup())
    with TestClient(app) as client:
        login = client.post('/api/auth/login', json={
            'username': 'owner-a', 'password': 'Synthetic-password-01!'})
        client.headers['X-CSRF-Token'] = login.json()['csrf_token']
        for endpoint in ('/api/ask/runs', '/api/ask/stock', '/api/ask/stock/stream'):
            response = client.post(endpoint, json={'question': 'synthetic'})
            assert response.status_code == 503
            message = response.json()['detail']['message']
            assert '工具调用' in message
            assert '管理' in message


def test_unselected_model_explains_personal_scene_selection(owners):
    from services.ai_gate import AIUnavailable, require_route_ready
    import pytest
    admin, _, _ = owners
    with user_scope(admin), pytest.raises(AIUnavailable) as caught:
        asyncio.run(require_route_ready('/api/ask/runs'))
    assert '问答' in str(caught.value)
    assert '主模型' in str(caught.value)


def test_text_and_tools_model_passes_readiness_gate(owners):
    from services import model_catalog
    from services.ai_gate import require_route_ready
    admin, _, _ = owners

    async def exercise():
        with user_scope(admin):
            await model_catalog.save_model(dict(
                id='with-tools', protocol='ollama', endpoint='http://localhost:11434',
                model='synthetic', local=True, capabilities=['text', 'tools']), expected_version=0)
            await model_catalog.save_preferences(local_only=True, scenes={'ask': ['with-tools']})
            await require_route_ready('/api/ask/runs')

    asyncio.run(exercise())
