import asyncio
import pytest
from tests.test_ownership_portfolio import owners
from services.user_context import user_scope


def test_admin_data_configuration_is_independent_of_models(owners):
    from services import data_sources as ds
    a,b,_=owners
    async def exercise():
        with user_scope(a):
            await ds.save_policy({'quotes':{'A':['tencent','sina'],'HK':['sina','tencent'],'US':['sina','tencent']},'kline':['tencent','eastmoney'],'news':['em','cls']},expected_version=0)
            state=await ds.overview()
            assert state['policy']['quotes']['A']==['tencent','sina']
        with user_scope(b):
            with pytest.raises(Exception):
                await ds.overview()
            assert (await ds.policy())['kline']==['tencent','eastmoney']
    asyncio.run(exercise())


def test_kline_policy_preserves_adjustment_and_never_uses_raw_sina(owners,monkeypatch):
    import pandas as pd
    from services import market_data as md,data_sources as ds
    a,b,_=owners
    md._cache.clear()
    async def no_cache(*args,**kw): return []
    import database
    monkeypatch.setattr(database,'get_cached_klines',no_cache)
    calls=[]
    def fail(*args):
        calls.append('eastmoney')
        return pd.DataFrame()
    def good(*args):
        calls.append('tencent')
        return pd.DataFrame([{'日期':'2026-10-16','收盘':10,'开盘':9,'最高':11,'最低':9,'成交量':100,'成交额':1000}])
    monkeypatch.setattr(md,'_fetch_history_em',fail)
    monkeypatch.setattr(md,'_kline_tencent_a',good)
    async def exercise():
        frame=await md._get_historical_data_inner('600000',60)
        assert frame.attrs['adjustment']=='qfq' and frame.attrs['source_id']=='tencent'
        assert calls==['eastmoney','tencent']
    asyncio.run(exercise())
