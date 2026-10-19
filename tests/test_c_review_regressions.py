import asyncio
from tests.test_ownership_portfolio import owners
from services.user_context import user_scope


def test_disabled_after_claim_never_starts_sending(owners, monkeypatch):
    from services import notification_delivery as d, notifications as n
    from services.notification_adapters import pushplus
    _, uid, _ = owners
    sent = []
    monkeypatch.setattr(pushplus, 'send', lambda config, summary: sent.append(config['token']) or 'accepted')
    original = d._claim
    async def claim_then_disable(identifier, now):
        row = await original(identifier, now)
        if row:
            with user_scope(uid):
                await d.activate(None)
                await d.delete_channel(row['channel_id'])
        return row
    monkeypatch.setattr(d, '_claim', claim_then_disable)
    async def run():
        with user_scope(uid):
            c = await d.save_channel({'kind':'pushplus','name':'test','config':{'token':'synthetic'}},expected_version=0)
            await d.activate(c['id'])
            await n.emit('risk','review',title='test')
        await d.deliver_pending()
        assert sent == []
    asyncio.run(run())


def test_chain_quote_never_replaces_today_with_old_stale_price(monkeypatch):
    from services import stock_agent as a, market_data as m
    from datetime import datetime, timedelta
    import pandas as pd
    today=datetime.now().date()
    frame=pd.DataFrame({'日期':[(today-timedelta(days=69-i)).isoformat() for i in range(70)],'收盘':[10.0]*70,'成交量':[100]*70})
    async def resolve(code):return {'code':'600000','name':'test'}
    async def history(*args):return frame
    async def quotes(*args):return {'600000':{'price':20.0,'quality':'stale','source_at':'2026-01-01T15:00:00+08:00'}}
    monkeypatch.setattr(a,'_tool_resolve_stock',resolve)
    monkeypatch.setattr(m,'get_historical_data',history)
    monkeypatch.setattr(m,'get_realtime_quotes',quotes)
    result=asyncio.run(a._tool_chain_quote(['600000']))
    assert result['stocks'][0]['pct_5d']==0.0


def test_empty_provider_ack_is_unknown_not_retryable(monkeypatch):
    from services.notification_adapters import pushplus, feishu
    monkeypatch.setattr(pushplus,'post_json',lambda *args,**kwargs:{})
    monkeypatch.setattr(feishu,'post_json',lambda *args,**kwargs:{})
    assert pushplus.send({'token':'synthetic'},'test')=='unknown'
    assert feishu.send({'webhook_url':'https://open.feishu.cn/open-apis/bot/v2/hook/synthetic'},'test')=='unknown'


def test_article_redirect_stops_after_user_closes_search(owners,monkeypatch):
    import sqlite3
    from services import stock_agent as a, news, search
    _,uid,path=owners
    called=[]
    class Response:
        status_code=200
        is_permanent_redirect=False
        encoding='utf-8'
        text='<article><p>'+('公开新闻内容。'*100)+'</p></article>'
        def __init__(self,redirect):
            self.is_redirect=redirect
            self.headers={'Location':'https://example.com/second'} if redirect else {}
    class Session:
        def get(self,url,**kwargs):
            called.append(url)
            if len(called)==1:
                with sqlite3.connect(path) as db:
                    db.execute('INSERT OR REPLACE INTO search_preferences VALUES (?,0)',(uid,))
            return Response(len(called)==1)
    monkeypatch.setattr(news._requests,'Session',Session)
    monkeypatch.setattr(news,'_host_is_public',lambda host:True)
    async def run():
        with user_scope(uid):
            token=a._search_urls.set({'https://example.com/first'})
            try:result=await a._tool_read_url('https://example.com/first')
            finally:a._search_urls.reset(token)
            assert (await search.preference())['enabled'] is False
        assert called==['https://example.com/first']
        assert result.get('full_text') is not True and 'error' in result
    asyncio.run(run())


def test_fund_constituent_obeys_disabled_hk_quote_policy(owners,monkeypatch):
    from services import data_sources as ds, market_data as md, fund_holdings as fh
    admin,uid,_=owners
    calls=[]
    monkeypatch.setattr(md,'_fetch_hk_stock_quote',lambda code:calls.append(code) or {'price':10,'change_pct':1})
    async def run():
        with user_scope(admin):
            await ds.save_policy({'quotes':{'A':[],'HK':[],'US':[]},'kline':[],'news':[]},expected_version=0)
        with user_scope(uid):
            quote=await fh.fetch_holding_quote({'market':'HK','code':'00700','name':'test','weight':1})
        assert calls==[]
        assert quote is None
    asyncio.run(run())


def test_fund_proxy_excludes_stale_prices_and_reports_coverage(monkeypatch):
    from services import fund_holdings as fh,fund_proxy as fp
    async def holdings(code):return [{'code':'600000','name':'新价','market':'CN_SH','weight':.5},{'code':'600001','name':'旧价','market':'CN_SH','weight':.5}]
    async def quote(h):return {**h,'price':10,'change_pct':2 if h['code']=='600000' else 100,'quality':'fresh' if h['code']=='600000' else 'stale'}
    monkeypatch.setattr(fh,'get_fund_top10',holdings)
    monkeypatch.setattr(fh,'fetch_holding_quote',quote)
    result=asyncio.run(fp._holdings_proxy('synthetic'))
    assert result['weighted_change_pct']==2
    assert result['coverage_pct']==50 and result['missing_constituents']==1
