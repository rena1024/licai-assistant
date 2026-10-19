import asyncio
from tests.test_ownership_portfolio import owners
from services.user_context import user_scope


def test_search_query_excludes_private_account_context(owners, monkeypatch):
    from services import search, stock_agent
    a,b,_=owners
    calls=[]
    async def external(query, **kw):
        calls.append(query)
        return {'status':'ok','results':[{'url':'https://example.com','title':'资讯'}]}
    monkeypatch.setattr(search,'search',external)
    async def exercise():
        with user_scope(b):
            out=await stock_agent._EXECUTORS['web_search']({'query':'账户 alice@example.com 持仓1000股 成本12.34元，600000 最近新闻'})
            assert out['status']=='ok'
            assert calls == ['600000 新闻']
            await search.preference(False)
            assert (await stock_agent._EXECUTORS['web_search']({'query':'600000 新闻'}))['status']=='disabled'
            assert len(calls)==1
    asyncio.run(exercise())


def test_search_disabled_blocks_article_retrieval_too(owners,monkeypatch):
    from services import search,stock_agent
    a,b,_=owners
    calls=[]
    monkeypatch.setattr(stock_agent,'_fetch_url_markdown_sync',lambda u:calls.append(u))
    async def exercise():
        with user_scope(b):
            await search.preference(False)
            assert 'error' in await stock_agent._tool_read_url('https://example.com/article')
            assert calls==[]
    asyncio.run(exercise())


def test_search_rechecks_preference_at_physical_send(owners,monkeypatch):
    from services import search
    from services.search_adapters import tavily
    from tests.test_search_quota import config
    a,b,_=owners
    called=[]
    monkeypatch.setattr(tavily,'search',lambda *args:called.append(1))
    original=search.send
    async def changed(*args):
        await search.preference(False)
        return await original(*args)
    monkeypatch.setattr(search,'send',changed)
    async def exercise():
        with user_scope(a):await search.save_channel(config(),expected_version=0)
        with user_scope(b):await search.search('600000 新闻',now=1792375200)
        assert called==[]
    asyncio.run(exercise())
