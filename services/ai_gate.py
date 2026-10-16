"""批次 A 的服务端关闭门槛；B 部分受控运行时完成前没有开启开关。"""

class AIUnavailable(RuntimeError):
    code='controlled_ai_not_ready'
    def __init__(self):
        super().__init__('受控 AI 尚未开放，当前可继续使用行情、资讯和手工理财功能')


def require_controlled_ai():
    raise AIUnavailable()


AI_ROUTES={
    ('POST','/api/ask/stock'),('POST','/api/ask/stock/stream'),('POST','/api/ask/runs'),('POST','/api/ask/deep-dive'),
    ('POST','/api/briefing/refresh'),('POST','/api/briefings/refresh'),('POST','/api/settings/llm/test'),('POST','/api/settings/rules/mine'),
    ('GET','/api/news/digest'),('GET','/api/news/daily-review'),('POST','/api/news/interpret'),
    ('GET','/api/portfolio/trade-review-ai'),('GET','/api/market/sentiment-ai'),('GET','/api/sector/trend-ai'),('POST','/api/sector/why'),
}
