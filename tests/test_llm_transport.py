import pytest
from services.model_catalog import ModelConfig
from services.llm_types import LLMError
from tests.test_budget_ledger import PRICE


@pytest.fixture
def recorded_http(monkeypatch):
    from services import llm_runtime, proxy_config
    import requests
    session = requests.Session()
    calls = []
    def post(url, **kwargs):
        calls.append((url, kwargs))
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"ok":true}'
        response._content_consumed = True
        return response
    monkeypatch.setattr(session, 'post', post)
    monkeypatch.setattr(llm_runtime.requests, 'Session', lambda: session)
    monkeypatch.setattr(proxy_config, 'get_proxy', lambda: '')
    return session, calls


@pytest.mark.parametrize('protocol,endpoint,path', [
    ('deepseek', 'https://api.deepseek.com', '/chat/completions'),
    ('deepseek', 'https://api.deepseek.com/v1', '/chat/completions'),
    ('anthropic', 'https://api.anthropic.com', '/v1/messages'),
    ('openai', 'https://api.openai.com', '/v1/responses'),
    ('qwen', 'https://dashscope.aliyuncs.com/compatible-mode/v1', '/chat/completions'),
    ('gemini', 'https://generativelanguage.googleapis.com', '/v1beta/models/synthetic:generateContent'),
])
@pytest.mark.parametrize('proxy', ['', 'http://127.0.0.1:7890'])
def test_official_cloud_api_uses_domain_with_default_tls(monkeypatch, recorded_http, protocol, endpoint, path, proxy):
    from services import llm_runtime, proxy_config
    import socket
    def no_pre_resolution(*args, **kwargs):
        raise AssertionError('官方 HTTPS 域名应交由连接层解析，兼容代理 Fake-IP')
    monkeypatch.setattr(socket, 'getaddrinfo', no_pre_resolution)
    monkeypatch.setattr(proxy_config, 'get_proxy', lambda: proxy)
    model = ModelConfig(id='official', protocol=protocol, endpoint=endpoint, model='synthetic', api_key='synthetic', price=PRICE)
    session, calls = recorded_http
    assert llm_runtime.send_http(model, {'synthetic': True}, path, {'Authorization': 'Bearer synthetic'}) == {'ok': True}
    url, kwargs = calls[0]
    assert url == endpoint + path
    assert kwargs['allow_redirects'] is False
    assert kwargs.get('verify', session.verify) is True
    assert type(session.get_adapter(url)) is llm_runtime.requests.adapters.HTTPAdapter
    assert kwargs['headers']['Authorization'] == 'Bearer synthetic'
    assert session.trust_env is False
    assert session.proxies == ({'http': proxy, 'https': proxy} if proxy else {})


@pytest.mark.parametrize('endpoint', [
    'https://custom.synthetic.example',
    'https://api.deepseek.com.attacker.example',
    'https://api.deepseek.com:8443',
])
@pytest.mark.parametrize('address', ['198.18.2.243', '127.0.0.1', '10.0.0.1', '169.254.169.254', '::1'])
def test_custom_cloud_endpoints_keep_nonpublic_address_protection(monkeypatch, recorded_http, endpoint, address):
    from services import llm_runtime, proxy_config
    import socket
    monkeypatch.setattr(proxy_config, 'get_proxy', lambda: 'http://127.0.0.1:7890')
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, 443))])
    model = ModelConfig(id='custom', protocol='openai_compatible', endpoint=endpoint, model='synthetic', api_key='synthetic', price=PRICE)
    with pytest.raises(LLMError) as error:
        llm_runtime.send_http(model, {}, '/chat/completions', {})
    assert error.value.code == 'private_endpoint'
    assert recorded_http[1] == []


def test_custom_public_endpoint_pins_ip_and_preserves_tls_hostname(monkeypatch, recorded_http):
    from services import llm_runtime
    import socket
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('1.1.1.1', 443))])
    model = ModelConfig(id='custom', protocol='openai_compatible', endpoint='https://synthetic.example/v1', model='synthetic', api_key='synthetic', price=PRICE)
    llm_runtime.send_http(model, {}, '/chat/completions', {})
    session, calls = recorded_http
    assert calls[0][0] == 'https://1.1.1.1:443/v1/chat/completions'
    assert calls[0][1]['headers']['Host'] == 'synthetic.example'
    assert session.get_adapter(calls[0][0]).poolmanager.connection_pool_kw['assert_hostname'] == 'synthetic.example'


def test_cloud_dns_cannot_target_private_network(monkeypatch):
    from services import llm_runtime
    import socket
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))])
    m=ModelConfig(id='m',protocol='anthropic',endpoint='https://synthetic.example',model='test',api_key='synthetic',price=PRICE)
    with pytest.raises(LLMError,match='内网'):
        llm_runtime.send_http(m,{},'/v1/messages',{})


def test_ollama_remote_alias_is_rejected():
    from services.llm_runtime import validate_ollama_metadata
    with pytest.raises(LLMError):validate_ollama_metadata({'remote_host':'https://ollama.com','model_info':{}})
    with pytest.raises(LLMError):validate_ollama_metadata({'details':{}})
    validate_ollama_metadata({'details':{'family':'qwen'},'model_info':{'general.architecture':'qwen'}})
