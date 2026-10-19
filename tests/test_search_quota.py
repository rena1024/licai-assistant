import asyncio
import pytest
from tests.test_ownership_portfolio import owners
from services.user_context import user_scope


def config(provider='tavily', **kw):
    return dict(provider=provider, key='synthetic-key-'+provider, enabled=True,
        free_confirmed=True, auto_pay_disabled=True, exclusive_key=True, storage_allowed=True,
        remaining=2, credits_per_request=1, period='2026-10', verified_until=1800000000,
        evidence='Synthetic offline account verification', **kw)


def test_free_quota_unknown_or_exhausted_never_pays(owners, monkeypatch):
    from services import search
    a,b,_ = owners
    calls=[]
    async def send(provider, key, query):
        calls.append(provider)
        return [{'title':'公开信息','url':'https://example.com','snippet':'摘要'}]
    monkeypatch.setattr(search,'send',send)
    async def exercise():
        with user_scope(a):
            await search.save_channel(config(), expected_version=0)
            await search.save_channel(config('exa'), expected_version=0)
        with user_scope(b):
            for _ in range(4):
                assert (await search.search('公开市场新闻', now=1792375200))['status']=='ok'
            assert (await search.search('公开市场新闻', now=1792375200))['status']=='unavailable'
        assert calls == ['tavily','tavily','exa','exa']
        with user_scope(a):
            c = config(); c['remaining']=None; c['enabled']=False
            await search.save_channel(c, expected_version=1)
        with user_scope(b):
            assert (await search.search('公开市场新闻',now=1792375200))['status']=='unavailable'
        assert len(calls)==4
    asyncio.run(exercise())


def test_search_timeout_retains_hold_and_pauses(owners, monkeypatch):
    from services import search
    a,b,_=owners
    calls=[]
    async def send(*args):
        calls.append(1)
        raise TimeoutError()
    monkeypatch.setattr(search,'send',send)
    async def exercise():
        with user_scope(a):
            await search.save_channel(config(),expected_version=0)
        with user_scope(b):
            assert (await search.search('公开市场',now=1792375200))['status']=='unavailable'
            assert (await search.search('公开市场',now=1792375200))['status']=='unavailable'
        assert len(calls)==1
    asyncio.run(exercise())


def test_parallel_search_reservations_are_bounded_and_known_retries_accounted(owners,monkeypatch):
    from services import search
    from services.external_http import Rejected
    a,b,path=owners
    calls=[]
    async def send(*args):
        calls.append(1)
        await asyncio.sleep(0)
        return []
    monkeypatch.setattr(search,'send',send)
    async def exercise():
        with user_scope(a):
            await search.save_channel(config(),expected_version=0)
        with user_scope(b):
            results=await asyncio.gather(*(search.search('市场新闻',now=1792375200) for _ in range(8)))
            assert sum(r['status']=='ok' for r in results)==2
        assert len(calls)==2
    asyncio.run(exercise())


def test_explicit_unbilled_failure_can_retry_once_with_new_credit_reservation(owners,monkeypatch):
    from services import search
    from services.external_http import Rejected
    a,b,path=owners
    calls=[]
    async def send(*args):
        calls.append(1)
        if len(calls)==1: raise Rejected()
        return []
    monkeypatch.setattr(search,'send',send)
    async def exercise():
        with user_scope(a): await search.save_channel(config(),expected_version=0)
        with user_scope(b): assert (await search.search('市场新闻',now=1792375200))['status']=='ok'
        assert len(calls)==2
    asyncio.run(exercise())
