import asyncio
import sqlite3

import pytest

from services.external_ledger import compute_external_state


def action(id, kind, amount=0, shares=None, day='2026-01-01', **extra):
    return dict(id=id, action_type=kind, amount=amount, shares=shares, trade_date=day, **extra)


def test_fund_redemption_uses_average_cost_but_keeps_fifo_share_dates():
    rows = [action(1, 'BUY', 100, 100), action(2, 'ADD', 300, 100, '2026-01-02'),
            action(3, 'REDEEM', 150, 50, '2026-01-03')]
    state = compute_external_state(rows, 'FUND')
    assert state['cost_amount'] == 300
    assert state['shares'] == 150
    assert state['realized_pnl'] == 50
    assert [(lot['date'], lot['shares']) for lot in state['lots']] == [('2026-01-01', 50), ('2026-01-02', 100)]
    assert sum(lot['amount'] for lot in state['lots']) == 300
    # 同一批流水在加密资产上仍按 FIFO 结转。
    crypto = compute_external_state(rows, 'CRYPTO')
    assert crypto['cost_amount'] == 350
    assert crypto['realized_pnl'] == 100


def test_average_cost_moves_on_new_buy_not_redemption_and_excludes_dividend():
    rows = [action(1, 'BUY', 100, 100), action(2, 'ADD', 300, 100, '2026-01-02'),
            action(3, 'REDEEM', 150, 50, '2026-01-03'),
            action(4, 'ADD', 300, 50, '2026-01-04'),
            action(5, 'DIVIDEND', 20, day='2026-01-05'),
            action(6, 'REDEEM', 400, 100, '2026-01-06'),
            action(7, 'ADD', 999, 999, '2026-01-07', status='pending')]
    state = compute_external_state(rows, 'FUND')
    assert state['shares'] == 100
    assert state['cost_amount'] == 300
    assert state['realized_pnl'] == 170
    assert state['income_realized'] == 20
    assert state['diluted_cost'] == 130
    # 期末市值 + 历史回款 + 分红 - 全部投入 = 浮动 + 全部已实现。
    assert 500 - state['cost_amount'] + state['realized_pnl'] == 500 + 150 + 400 + 20 - 700


def test_fund_cost_uses_actual_payment_including_fees_not_rounded_nav():
    rows = [action(1, 'BUY', 101, 100, fee=1, unit_price=1),
            action(2, 'ADD', 300, 100, '2026-01-02', fee=0, unit_price=2.9999),
            action(3, 'REDEEM', 150, 50, '2026-01-03', fee=5)]
    state = compute_external_state(rows, 'FUND')
    assert state['cost_amount'] == 300.75
    assert state['realized_pnl'] == 49.75  # 赎回金额已扣费，不能再次减手续费。


@pytest.mark.parametrize('rebuy_day', ['2026-01-03', '2026-01-04'])
def test_average_cost_resets_after_full_redemption(rebuy_day):
    rows = [action(1, 'BUY', 100, 100), action(2, 'ADD', 300, 100, '2026-01-02'),
            action(3, 'REDEEM', 500, 200, '2026-01-03'),
            action(4, 'ADD', 120, 30, rebuy_day)]
    state = compute_external_state(rows, 'FUND')
    assert state['cost_amount'] == 120
    assert state['shares'] == 30
    assert state['realized_pnl'] == 100


def test_split_and_backfill_preserve_average_cost():
    rows = [action(1, 'BUY', 100, 100), action(3, 'REDEEM', 200, 100, '2026-01-03'),
            action(4, 'SPLIT', shares=2, day='2026-01-02'),
            action(5, 'ADD', 300, 100, '2026-01-01', trade_time='10:00')]
    state = compute_external_state(rows, 'FUND')
    assert state['cost_amount'] == 300
    assert state['shares'] == 300
    assert state['realized_pnl'] == 100


def test_fund_api_separates_holding_profit_from_realized(monkeypatch):
    from config import config
    from database import init_db, add_external_asset, add_external_action
    from api import assets_routes

    async def quote(_code):
        return {'nav': 3, 'nav_date': '2026-01-04'}
    monkeypatch.setattr(assets_routes, 'get_fund_quote', quote)
    from services import fund_proxy, fund_nav_model
    async def no_proxy(*_args):
        return None
    monkeypatch.setattr(fund_proxy, 'get_fund_proxy', no_proxy)
    monkeypatch.setattr(fund_nav_model, 'ensure_fit', no_proxy)
    monkeypatch.setattr(fund_nav_model, 'estimate', lambda *_args: None)

    async def run():
        await init_db()
        with sqlite3.connect(config.db_path) as db:
            db.execute("INSERT INTO users (id,uuid,username,password_hash,role,created_at) VALUES (1,'owner','owner','unused','user',0)")
        asset = await add_external_asset('FUND', '000711', '样例基金', '支付宝', 0, shares=0, user_id=1)
        for row in [action(1, 'BUY', 100, 100), action(2, 'ADD', 300, 100, '2026-01-02'),
                    action(3, 'REDEEM', 150, 50, '2026-01-03'), action(4, 'DIVIDEND', 20, day='2026-01-02')]:
            await add_external_action(asset, row['action_type'], amount=row['amount'], shares=row['shares'],
                                      trade_date=row['trade_date'], user_id=1)
        result = await assets_routes.list_assets()
        fund = result['assets'][0]
        assert fund['cost_amount'] == 300
        assert fund['current_value'] == 450
        assert fund['holding_dividend'] == 15
        assert fund['pnl'] == 165
        assert fund['pnl_pct'] == 55
        assert fund['realized_pnl'] == 70
        assert result['summary']['total_cost'] == 300
        realized = await assets_routes.assets_realized()
        assert realized['items'][0]['closed_realized'] == 55
        assert fund['pnl'] + realized['items'][0]['closed_realized'] == 220
    asyncio.run(run())
