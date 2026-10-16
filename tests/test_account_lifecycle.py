import asyncio
import pytest
from tests.test_ownership_portfolio import owners
from services.user_context import user_scope


def test_disable_cancels_future_steps_without_deleting_data(owners):
    import database as db
    from services import accounts,auth
    from services.task_context import ensure_active
    a,b,_=owners
    async def exercise():
        await db.add_holding('600000','B',100,10,user_id=b)
        session=await auth.login(username='owner-b',password='Synthetic-password-02!')
        await accounts.set_user_active(actor_id=a,user_id=b,active=False)
        with pytest.raises(auth.AuthError):
            await auth.authenticate(session.token)
        with user_scope(b):
            with pytest.raises(accounts.AccountError):
                await ensure_active()
        assert await db.get_holding('600000',user_id=b)
        await accounts.set_user_active(actor_id=a,user_id=b,active=True)
        with pytest.raises(auth.AuthError):
            await auth.authenticate(session.token)
        with pytest.raises(accounts.AccountError):
            await accounts.set_user_active(actor_id=a,user_id=a,active=False)
    asyncio.run(exercise())


def test_automatic_ai_defaults_off_and_disabled_users_not_dispatched(owners,monkeypatch):
    from services import scheduler,accounts
    a,b,_=owners
    seen=[]
    async def non_ai():
        from services.user_context import require_user_id
        seen.append(require_user_id())
    async def ai():
        raise AssertionError('默认不能运行自动 AI')
    async def exercise():
        await accounts.set_user_active(actor_id=a,user_id=b,active=False)
        await scheduler.dispatch_personal(non_ai)
        await scheduler.dispatch_personal(ai,ai=True)
    asyncio.run(exercise())
    assert seen==[a]


def test_public_close_jobs_preserve_non_ai_archiving(monkeypatch):
    from services import scheduler
    from datetime import datetime, timezone
    seen=[]
    async def archive():seen.append('archive')
    async def pool(**kwargs):
        assert kwargs == {'public':True}
        seen.append('pool')
    async def tags():seen.append('tags')
    monkeypatch.setattr('services.sector_share.archive_today',archive)
    monkeypatch.setattr('services.limit_up_pool.sync_today',pool)
    monkeypatch.setattr('services.stock_tags.sync_today',tags)
    asyncio.run(scheduler.archive_public_close())
    assert seen==['archive','pool','tags']


def test_reenable_dca_preserves_asset_market_calendar(owners,monkeypatch):
    import database as db
    from services import accounts,dca
    a,b,_=owners
    markets=[]
    def due(*args,market='CN',**kwargs):
        markets.append(market)
        return '2099-10-01'
    monkeypatch.setattr(dca,'initial_next_due',due)
    async def exercise():
        asset=await db.add_external_asset('FUND','test-nasdaq','纳斯达克100基金','',100,user_id=b)
        await db.add_dca_schedule(asset,'amount',10,frequency='daily_trading',next_due='2000-01-01',user_id=b)
        await accounts.set_user_active(actor_id=a,user_id=b,active=False)
        await accounts.set_user_active(actor_id=a,user_id=b,active=True)
    asyncio.run(exercise())
    assert markets==['US']
