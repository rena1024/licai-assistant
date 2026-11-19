import asyncio
import sqlite3
from datetime import date, timedelta

import pytest
from fastapi import HTTPException

from api.assets_routes import CashDividendCreate, add_cash_dividend, delete_action
from config import config
from database import init_db, add_external_asset, add_external_action, get_external_asset, list_external_actions
from services.user_context import user_scope


async def setup_fund():
    await init_db()
    with sqlite3.connect(config.db_path) as db:
        db.execute("INSERT INTO users (id,uuid,username,password_hash,role,created_at) VALUES (1,'owner','owner','unused','user',0)")
    asset = await add_external_asset('FUND', '000711', '样例基金', '支付宝', 1000, shares=1000, user_id=1)
    await add_external_action(asset, 'BUY', amount=1000, shares=1000, unit_price=1,
                              trade_date='2020-01-01', user_id=1)
    return asset


def test_historical_cash_dividend_updates_income_without_changing_shares_or_cost():
    async def run():
        asset = await setup_fund()
        await add_external_action(asset, 'REDEEM', amount=600, shares=500,
                                  trade_date='2026-02-01', user_id=1)
        result = await add_cash_dividend(asset, CashDividendCreate(
            amount=123.45, trade_date='2021-05-20', note='历史到账'))
        rows = await list_external_actions(asset, user_id=1)
        dividend = next(a for a in rows if a['action_type'] == 'DIVIDEND')
        assert dividend['trade_date'] == '2021-05-20'
        assert dividend['amount'] == 123.45
        assert dividend['note'] == '历史到账'
        assert dividend['status'] == 'confirmed'
        assert not dividend['shares'] and not dividend['unit_price'] and not dividend['fee']
        assert result['state']['realized_pnl'] == 223.45
        assert result['state']['shares'] == 500
        assert result['state']['cost_amount'] == 500
        saved = await get_external_asset(asset, user_id=1)
        assert saved['shares'] == 500 and saved['cost_amount'] == 500
        deleted = await delete_action(asset, dividend['id'])
        assert deleted['state']['realized_pnl'] == 100
    asyncio.run(run())


@pytest.mark.parametrize('amount', [0, -1, float('nan'), float('inf')])
def test_invalid_dividend_amount_cannot_write(amount):
    async def run():
        asset = await setup_fund()
        with pytest.raises(HTTPException) as error:
            await add_cash_dividend(asset, CashDividendCreate(amount=amount, trade_date='2021-05-20'))
        assert error.value.status_code == 400
        assert len(await list_external_actions(asset, user_id=1)) == 1
    asyncio.run(run())


@pytest.mark.parametrize('day', ['', '2021-02-29', '20210520', (date.today() + timedelta(days=1)).isoformat()])
def test_invalid_dividend_date_cannot_write(day):
    async def run():
        asset = await setup_fund()
        with pytest.raises(HTTPException) as error:
            await add_cash_dividend(asset, CashDividendCreate(amount=10, trade_date=day))
        assert error.value.status_code == 400
        assert len(await list_external_actions(asset, user_id=1)) == 1
    asyncio.run(run())


def test_dividend_requires_owned_fund():
    async def run():
        asset = await setup_fund()
        with user_scope(2), pytest.raises(HTTPException) as error:
            await add_cash_dividend(asset, CashDividendCreate(amount=10, trade_date='2021-05-20'))
        assert error.value.status_code == 404
        other = await add_external_asset('CRYPTO', 'BTC', 'Bitcoin', '', 10, shares=1, user_id=1)
        with pytest.raises(HTTPException) as error:
            await add_cash_dividend(other, CashDividendCreate(amount=10, trade_date='2021-05-20'))
        assert error.value.status_code == 400
        assert await list_external_actions(other, user_id=1) == []
    asyncio.run(run())


def test_cash_dividend_http_contract(authenticated_client):
    asset = asyncio.run(add_external_asset('FUND', '000711', '样例基金', '', 0, shares=0, user_id=1))
    response = authenticated_client.post(f'/api/assets/{asset}/dividends', json={
        'amount': 88.8, 'trade_date': '2021-05-20', 'note': ' 历史分红 ',
    })
    assert response.status_code == 200
    assert response.json()['state']['realized_pnl'] == 88.8
    history = authenticated_client.get(f'/api/assets/{asset}/actions').json()
    assert history['actions'][0]['note'] == '历史分红'
    invalid = authenticated_client.post(f'/api/assets/{asset}/dividends', json={
        'amount': 0, 'trade_date': '2021-05-20',
    })
    assert invalid.status_code == 400
    missing = authenticated_client.post(f'/api/assets/{asset}/dividends', json={'amount': 10})
    assert missing.status_code == 422
