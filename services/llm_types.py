"""厂商无关的推理合同；金额使用 Decimal，正文不进入计量账本。"""
from dataclasses import dataclass, field
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

Capability = Literal['text', 'tools', 'image', 'structured']


class ModelRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    messages: list[dict]
    system: str = ''
    tools: list[dict] = Field(default_factory=list)
    response_schema: dict | None = None
    # None 表示由受支持的模型服务决定输出额度。
    output_limit: int | None = Field(default=2048, ge=1, le=8192)
    capability: set[Capability] = Field(default_factory=lambda: {'text'})

    def required_capabilities(self):
        result = set(self.capability) | {'text'}
        if self.tools:
            result.add('tools')
        if self.response_schema:
            result.add('structured')
        def has_image(content):
            if not isinstance(content,list):return False
            return any(isinstance(b,dict) and (b.get('type') in {'image','image_url','input_image'}
                or (b.get('type')=='tool_result' and has_image(b.get('content')))) for b in content)
        if any(has_image(m.get('content')) for m in self.messages):
            result.add('image')
        return result


class Usage(BaseModel):
    model_config = ConfigDict(extra='forbid')
    # input 包含所有缓存类别；output 包含推理，二者的细分不能重复收费。
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)
    cache_write_tokens: int = Field(default=0, ge=0)
    cache_write_1h_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)


@dataclass(frozen=True)
class CallContext:
    owner_id: int | None
    scene: str
    task_id: str
    purpose: Literal['user', 'diagnostic'] = 'user'
    background: bool = False


@dataclass
class ModelResult:
    content: list[dict]
    usage: Usage | None
    finish_reason: str = 'stop'
    model_id: str = ''
    provider_request_id: str = ''
    tool_calls: list[dict] = field(default_factory=list)
    fallback_reason: str = ''


class LLMError(RuntimeError):
    def __init__(self, code, message=None, *, failure_codes=()):
        self.code = code
        self.failure_codes = tuple(failure_codes)
        super().__init__(message or code)
