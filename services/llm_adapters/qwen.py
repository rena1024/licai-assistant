"""通义千问 OpenAI 兼容模式；显式使用 compatible-mode 根路径。"""
from services.llm_adapters import openai_compatible as chat


def encode(model,request):
    payload,path,headers=chat.encode(model,request)
    # 管理员填地域对应的 compatible-mode/v1 基础端点，不猜测区域或厂商模型。
    payload['enable_thinking']=False
    return payload,path,headers


decode=chat.decode
