from datetime import datetime, timezone
from services.quote_sources import quality
from tests.test_ownership_portfolio import owners


def test_closed_market_is_not_outage_and_missing_price_is_not_zero():
    q = {'price': 10, 'market':'US', 'source_at':'2026-07-17T16:00:00-04:00'}
    now = datetime(2026,7,18,12,tzinfo=timezone.utc)
    assert quality(q, now=now)['quality'] == 'last_trade'
    assert quality(q, now=now, failed=True)['quality'] == 'stale'
    assert quality({'price':0}, now=now)['price'] is None
    from services.quote_sources import coverage
    data = coverage([{'shares':100,'market_value':1000,'quote_quality':'stale'},
                     {'shares':100,'market_value':None,'quote_quality':'missing'}])
    assert data == {'held':2,'covered':1,'stale':1,'missing':1,'coverage_pct':50.0,'complete':False}


def test_ai_quote_preserves_quality(monkeypatch):
    import asyncio
    from services import stock_agent, market_data
    async def fake(codes):
        return {codes[0]: {'price':10,'source_id':'sina','quality':'stale',
            'stale_reason':'sources_unavailable','source_at':'2026-10-16T07:00:00Z','fetched_at':'2026-10-19T02:00:00Z'}}
    monkeypatch.setattr(market_data, 'get_realtime_quotes', fake)
    q = asyncio.run(stock_agent._tool_get_quote('600000'))
    assert q['quality'] == 'stale'
    assert '不可称为最新' in q['data_limitations']


def test_old_closed_quote_is_not_current_last_trade():
    q={'price':10,'market':'US','source_at':'2026-07-10T16:00:00-04:00'}
    assert quality(q,now=datetime(2026,7,18,12,tzinfo=timezone.utc))['quality']=='stale'


def test_missing_quote_snapshot_is_not_recorded_as_complete(owners,monkeypatch):
    import asyncio
    import api.portfolio_routes as pr
    import api.assets_routes as ar
    from services import portfolio_snapshot as ps
    from services.user_context import user_scope
    a,b,_=owners
    async def holdings(): return [{'shares':100,'stock_code':'600000','market_value':None,'quote_quality':'missing'}]
    async def assets(): return {'assets':[{'id':1,'current_value':100}]}
    monkeypatch.setattr(pr,'list_holdings',holdings)
    monkeypatch.setattr(ar,'list_assets',assets)
    with user_scope(b):
        assert asyncio.run(ps.take_snapshot()) is None


def test_portfolio_missing_quote_keeps_null_values(owners,monkeypatch):
    import asyncio
    import database
    import api.portfolio_routes as pr
    from services.user_context import user_scope
    a,b,_=owners
    async def quotes(codes): return {c:quality({'price':None,'market':'A'}) for c in codes}
    async def sector(code): return '银行'
    monkeypatch.setattr(pr,'get_realtime_quotes',quotes)
    monkeypatch.setattr(pr,'get_stock_sector',sector)
    async def exercise():
        await database.add_holding('600000','测试',100,10,user_id=b)
        await database.add_position_action('600000','BUY',10,100,user_id=b)
        with user_scope(b):
            row=(await pr.list_holdings())[0]
            assert row.current_price is None and row.market_value is None and row.unrealized_pnl is None
    asyncio.run(exercise())


def test_news_aggregation_preserves_original_sources_and_unknown_time():
    from services.news import aggregate_items
    rows=aggregate_items([('em',[{'title':'同一消息','source':'原媒体A','url':'https://example.com/a','time':'2026-10-19 10:00'}]),
                          ('cls',[{'title':'同一消息','source':'原媒体B','url':'https://example.com/b','time':''}])],fetched_at='fixed')
    assert len(rows)==1 and len(rows[0]['sources'])==2
    assert rows[0]['sources'][1]['source_at'] is None
    assert rows[0]['fetched_at']=='fixed'


def test_missing_price_does_not_send_cost_as_quote_to_briefing_model(owners,monkeypatch):
    import asyncio
    import database
    from services import morning_briefing as mb,market_data as md
    from services.user_context import user_scope
    a,b,_=owners
    sent=[]
    async def quote(codes):return {c:{'price':None,'quality':'missing'} for c in codes}
    async def generate(*args,**kw):
        sent.append(kw.get('current_price'))
        return {'summary':'不应调用'}
    monkeypatch.setattr(md,'get_realtime_quotes',quote)
    monkeypatch.setattr(mb,'generate_briefing_for_stock',generate)
    async def exercise():
        await database.add_holding('600000','测试',100,10,user_id=b)
        await database.add_position_action('600000','BUY',10,100,user_id=b)
        with user_scope(b):
            rows=await mb.generate_all_briefings()
            assert sent==[]
            assert rows[0]['current_price'] is None and rows[0]['pnl_pct'] is None
    asyncio.run(exercise())
