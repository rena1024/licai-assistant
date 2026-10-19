"""旧业务调用签名的薄兼容层；模型、权限、发送及计量由统一运行时决定。"""
import asyncio
import uuid
from services.llm_types import CallContext, ModelRequest
from services.user_context import require_user_id


class LLMOverloaded(RuntimeError):
    """兼容已有业务异常分支；实际重试只允许在运行时创建独立 attempt。"""
    def __init__(self,message,status=529):
        self.status=status
        super().__init__(message)


class ModelText(str):
    def __new__(cls,text,model_id):
        value=super().__new__(cls,text)
        value.model_id=model_id
        return value


class ErrorText(str):
    def __new__(cls,error):
        value=super().__new__(cls,str(error))
        value.code=getattr(error,'code','provider_error')
        return value


def configure_llm(**kwargs):
    """旧配置加载保持无副作用；不会导入环境密钥或建立认证回退。"""


def get_llm_config():
    return {'base_url':'','api_key':'','has_api_key':False,'api_key_header':'',
            'api_key_prefix':'','proxy':'','model_map':{},'using_oauth_fallback':False,
            'deprecated':True,'message':'请使用模型服务与我的 AI 设置'}


def call_claude_messages(messages,system=None,model=None,max_tokens=2048,tools=None,*,scene=None):
    from services import llm_runtime as runtime
    context=CallContext(require_user_id(),scene or runtime._scene.get(),runtime._task.get() or uuid.uuid4().hex,
                        background=runtime._background.get())
    request=ModelRequest(messages=messages,system=system or '',output_limit=max_tokens,tools=tools or [])
    result=asyncio.run(runtime.generate(context,request))
    return {'content':result.content,'stop_reason':result.finish_reason,
            'usage':result.usage.model_dump() if result.usage else None,
            'model_id':result.model_id,'fallback_reason':result.fallback_reason}


def call_claude(user_prompt,system=None,model=None,max_tokens=2048,_retried=False,*,scene=None):
    data=call_claude_messages([{'role':'user','content':user_prompt}],system,None,max_tokens,scene=scene)
    if data.get('stop_reason')=='max_tokens':
        from services.llm_types import LLMError
        raise LLMError('incomplete_response','模型达到输出上限，回答未完成')
    text=''.join(b.get('text','') for b in data['content'] if b.get('type')=='text')
    if not text:
        from services.llm_types import LLMError
        raise LLMError('empty_response','模型没有返回正文，请调整配置或手动重试')
    return ModelText(text,data['model_id'])


def test_connection():
    from services.llm_types import LLMError
    raise LLMError('diagnostic_requires_model','请从模型服务选择模型并显式发起受预算控制的测试')
