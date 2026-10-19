import pytest
from services.model_catalog import ModelConfig
from services.llm_types import LLMError
from tests.test_budget_ledger import PRICE


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
