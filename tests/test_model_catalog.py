import asyncio
import pytest
from tests.test_ownership_portfolio import owners
from services.user_context import user_scope


def model(**overrides):
    return dict(id='local', protocol='ollama', endpoint='http://localhost:11434',
                model='qwen3:8b', local=True, capabilities=['text', 'tools'], **overrides)


def test_user_owns_scene_choice_and_invalid_config_preserves_version(owners):
    from services import model_catalog as catalog
    a, b, _ = owners
    async def exercise():
        with user_scope(a):
            first = await catalog.save_model(model(), expected_version=0)
            assert first['version'] == 1
        with user_scope(b):
            await catalog.save_preferences(local_only=True, scenes={'ask': ['local']})
            assert [m['id'] for m in await catalog.candidates('ask', {'tools'})] == ['local']
            assert await catalog.candidates('ask', {'image'}) == []
            with pytest.raises(Exception):
                await catalog.save_model(model(), expected_version=1)
        with user_scope(a):
            assert (await catalog.preferences())['scenes'] == {}
            with pytest.raises(ValueError):
                await catalog.save_model({**model(), 'endpoint': 'https://example.com'}, expected_version=1)
            assert (await catalog.list_models())[0]['version'] == 1
            await catalog.save_model({**model(), 'enabled': False}, expected_version=1)
        with user_scope(b):
            assert (await catalog.preferences())['scenes']['ask'] == ['local']
            assert await catalog.candidates('ask', {'text'}) == []
            await catalog.save_preferences(local_only=True, scenes={})
    asyncio.run(exercise())


@pytest.mark.parametrize('endpoint', ['file:///etc/passwd', 'http://localhost:11434@evil.test',
    'http://127.0.0.1:11434/api/chat?key=x', 'http://169.254.169.254', 'http://example.com'])
def test_endpoint_validation(endpoint):
    from services.model_catalog import ModelConfig
    with pytest.raises(ValueError):
        ModelConfig(**{**model(), 'endpoint': endpoint})
