"""无重试协议映射；只有运行时持有物理发送权限。"""
from services.llm_types import LLMError


def adapter(protocol):
    from importlib import import_module
    if protocol not in {'ollama','anthropic','openai','deepseek','qwen','gemini','openai_compatible'}:
        raise LLMError('unsupported_protocol')
    return import_module(f'services.llm_adapters.{protocol}')
