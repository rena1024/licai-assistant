import asyncio
import sqlite3

import pytest
from fastapi import HTTPException

from config import config
from database import init_db, add_external_asset, add_external_action, list_external_actions
from api.assets_routes import ActionPatch, ConfirmAction, confirm_action, patch_action


async def setup_fund():
    await init_db()
    with sqlite3.connect(config.db_path) as db:
        db.execute("INSERT INTO users (id,uuid,username,password_hash,role,created_at) VALUES (1,'owner','owner','unused','user',0)")
    asset = await add_external_asset('FUND', '000711', '样例基金', '支付宝', 1000, shares=1000, user_id=1)
    await add_external_action(asset, 'BUY', amount=1000, shares=1000, unit_price=1, fee=0,
                              trade_date='2026-01-01', user_id=1)
    return asset


@pytest.mark.parametrize('fee', [None, 0, 6])
def test_redeem_confirmation_uses_net_proceeds_and_gross_nav(fee):
    async def run():
        asset = await setup_fund()
        action = await add_external_action(asset, 'REDEEM', shares=1000, status='pending',
                                           trade_date='2026-01-02', user_id=1)
        net = 1200 - (fee or 0)
        result = await confirm_action(asset, action, ConfirmAction(amount=net, fee=fee))
        stored = next(a for a in await list_external_actions(asset, user_id=1) if a['id'] == action)
        assert stored['amount'] == net
        assert stored['unit_price'] == 1.2
        assert stored['fee'] == (fee or 0)
        assert result['state']['realized_pnl'] == net - 1000
    asyncio.run(run())


def test_buy_confirmation_still_derives_shares_from_net_subscription():
    async def run():
        asset = await setup_fund()
        action = await add_external_action(asset, 'ADD', amount=1206, status='pending',
                                           trade_date='2026-01-02', user_id=1)
        await confirm_action(asset, action, ConfirmAction(amount=1206, unit_price=1.2, fee=6))
        stored = next(a for a in await list_external_actions(asset, user_id=1) if a['id'] == action)
        assert stored['shares'] == 1000
        assert stored['amount'] == 1206
    asyncio.run(run())


def test_editing_saved_redemption_corrects_amount_and_realized_profit():
    async def run():
        asset = await setup_fund()
        action = await add_external_action(asset, 'REDEEM', amount=1206, shares=1000, unit_price=1.2,
                                           fee=6, trade_date='2026-01-02', user_id=1)
        await patch_action(asset, action, ActionPatch(amount=1194, shares=1000, unit_price=1.2, fee=6))
        stored = next(a for a in await list_external_actions(asset, user_id=1) if a['id'] == action)
        assert stored['amount'] == 1194
        assert stored['fee'] == 6
        assert stored['unit_price'] == 1.2
        from services.external_ledger import compute_external_state
        state = compute_external_state(await list_external_actions(asset, user_id=1), 'FUND')
        assert state['realized_pnl'] == 194
    asyncio.run(run())


@pytest.mark.parametrize('fee', [-1, float('inf'), float('nan')])
def test_invalid_confirmation_fee_cannot_be_saved(fee):
    async def run():
        asset = await setup_fund()
        action = await add_external_action(asset, 'REDEEM', shares=1000, status='pending',
                                           trade_date='2026-01-02', user_id=1)
        with pytest.raises(HTTPException) as error:
            await confirm_action(asset, action, ConfirmAction(amount=1194, fee=fee))
        assert error.value.status_code == 400
        stored = next(a for a in await list_external_actions(asset, user_id=1) if a['id'] == action)
        assert stored['status'] == 'pending'
    asyncio.run(run())
