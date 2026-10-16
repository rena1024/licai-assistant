"""私人设置的双用户隔离合同。"""
import asyncio
import pytest
from tests.test_ownership_portfolio import owners


def test_private_settings_do_not_share_keys(owners):
    import database as db
    a, b, _ = owners
    async def exercise():
        for uid, amount in ((a, 100), (b, 200)):
            await db.upsert_cashflow('2026-10', amount, 0, 0, user_id=uid)
            await db.set_config('max_daily_loss', str(amount), user_id=uid)
            await db.add_watchlist('600000', str(amount), amount, user_id=uid)
            await db.set_watchlist_groups('600000', [str(amount)], user_id=uid)
            await db.set_thesis('600000', str(amount), user_id=uid)
        assert (await db.get_cashflow('2026-10', user_id=a))['income'] == 100
        assert await db.get_config('max_daily_loss', user_id=b) == '200'
        assert (await db.list_watchlist(user_id=a))[0]['groups'] == ['100']
        assert (await db.get_thesis('600000', user_id=b))['thesis'] == '200'
        await db.delete_cashflow('2026-10', user_id=b)
        await db.delete_thesis('600000', user_id=b)
        assert await db.get_cashflow('2026-10', user_id=a)
        assert len(await db.get_thesis_revisions('600000', user_id=a)) == 1
    asyncio.run(exercise())


def test_foreign_broker_cannot_clear_own_default(owners):
    import database as db
    a, b, _ = owners
    async def exercise():
        aid = await db.add_broker('同名券商', .001, 5, .001, 5, user_id=a)
        bid = await db.add_broker('同名券商', .002, 5, .002, 5, user_id=b)
        await db.update_broker(aid, is_default=1, user_id=a)
        await db.update_broker(bid, is_default=1, user_id=a)
        assert (await db.get_default_broker(user_id=a))['id'] == aid
        await db.delete_broker(aid, user_id=b)
        assert any(x['id'] == aid for x in await db.list_brokers(user_id=a))
    asyncio.run(exercise())


def test_group_batch_cannot_create_foreign_membership(owners):
    import database as db
    a, b, _ = owners
    async def exercise():
        await db.add_watchlist('600000', 'A', 10, user_id=a)
        await db.reorder_watchlist(['600000'], scope='group', group='B', user_id=b)
        assert (await db.list_watchlist(user_id=a))[0]['groups'] == ['自选']
        assert await db.list_watchlist(user_id=b) == []
    asyncio.run(exercise())
