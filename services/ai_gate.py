"""批次 A 的服务端关闭门槛；B 部分受控运行时完成前没有开启开关。"""

class AIUnavailable(RuntimeError):
    code='controlled_ai_not_ready'
    def __init__(self, message='受控 AI 尚未开放，当前可继续使用行情、资讯和手工理财功能'):
        super().__init__(message)


def require_controlled_ai():
    raise AIUnavailable()


async def require_route_ready(path):
    from services.model_catalog import candidates
    scene = ('multi_lens' if path.endswith('deep-dive') else 'ask' if '/ask/' in path
             else 'briefing' if '/briefing' in path else 'news' if '/news/' in path
             else 'portfolio' if '/portfolio/' in path else 'market' if '/market/' in path
             else 'sector' if '/sector/' in path else 'rules')
    required = {'text','tools'} if scene=='ask' else {'text'}
    if path == '/api/settings/llm/test' or not await candidates(scene,required):
        if scene == 'ask':
            if await candidates(scene, {'text'}):
                raise AIUnavailable('问答模型未开放工具调用能力，无法启动分析。请到管理 → 模型服务，为问答候选模型开放“工具”能力并测试，再重试。')
            raise AIUnavailable('问答场景没有可用模型。请到设置 → 模型偏好，选择支持“文本”和“工具”能力的问答主模型；确认模型已启用，且符合“仅本地”模式限制。')
        raise AIUnavailable()


AI_ROUTES={
    ('POST','/api/ask/stock'),('POST','/api/ask/stock/stream'),('POST','/api/ask/runs'),('POST','/api/ask/deep-dive'),
    ('POST','/api/briefing/refresh'),('POST','/api/briefings/refresh'),('POST','/api/settings/llm/test'),('POST','/api/settings/rules/mine'),
    ('GET','/api/news/digest'),('GET','/api/news/daily-review'),('POST','/api/news/interpret'),
    ('GET','/api/portfolio/trade-review-ai'),('GET','/api/market/sentiment-ai'),('GET','/api/sector/trend-ai'),('POST','/api/sector/why'),
}
