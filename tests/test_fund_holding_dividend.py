import asyncio

import pytest

from services.external_ledger import compute_external_state


def action(id, kind, amount=0, shares=None, day='2026-01-01', **extra):
    return dict(id=id, action_type=kind, amount=amount, shares=shares, trade_date=day, **extra)


def test_partial_redemption_proportionally_moves_dividend_out_of_holding():
    rows = [action(1, 'BUY', 1000, 100), action(2, 'DIVIDEND', 100, day='2026-01-02'),
            action(3, 'ADD', 1000, 100, '2026-01-03'),
            action(4, 'REDEEM', 600, 50, '2026-01-04'),
            action(5, 'DIVIDEND', 30, day='2026-01-05'),
            action(6, 'REDEEM', 900, 75, '2026-01-06')]
    state = compute_external_state(rows, 'FUND')
    assert state['holding_dividend'] == 52.5  # (100 × 150/200 + 30) × 75/150
    assert state['income_realized'] == 130
    assert state['realized_pnl'] == 380
    assert state['cost_amount'] == 750
    assert state['shares'] == 75
    # 加密资产仍不将分红按基金持有收益口径分配。
    assert compute_external_state(rows, 'CRYPTO')['holding_dividend'] == 0


@pytest.mark.parametrize('rebuy_day', ['2026-01-03', '2026-01-04'])
def test_full_redemption_clears_holding_dividend_even_if_rebought_same_day(rebuy_day):
    rows = [action(1, 'BUY', 100, 100), action(2, 'DIVIDEND', 10, day='2026-01-02'),
            action(3, 'REDEEM', 120, 100, '2026-01-03'),
            action(4, 'ADD', 100, 100, rebuy_day)]
    state = compute_external_state(rows, 'FUND')
    assert state['holding_dividend'] == 0
    assert state['income_realized'] == 10
    assert state['realized_pnl'] == 30


def test_split_and_pending_do_not_change_holding_dividend():
    rows = [action(1, 'BUY', 100, 100), action(2, 'DIVIDEND', 10, day='2026-01-02'),
            action(3, 'SPLIT', shares=2, day='2026-01-03'),
            action(4, 'REDEEM', 60, 100, '2026-01-04'),
            action(5, 'REDEEM', 60, 100, '2026-01-05', status='pending'),
            action(6, 'DIVIDEND', 999, day='2026-01-06', status='pending')]
    assert compute_external_state(list(reversed(rows)), 'FUND')['holding_dividend'] == 5


def test_dividend_paid_without_any_holding_stays_in_history_only():
    state = compute_external_state([action(1, 'DIVIDEND', 10),
                                    action(2, 'BUY', 100, 100, '2026-01-02')], 'FUND')
    assert state['holding_dividend'] == 0
    assert state['realized_pnl'] == 10


@pytest.mark.parametrize('pending', [0, 2800])
def test_holding_profit_matches_platform_rounding_and_excludes_pending(monkeypatch, pending):
    from api import assets_routes

    # 用分段汇总的历史流水复现成本及分红分摊，避免依赖真实账号和行情。
    rows = [action(1, 'BUY', 2800, 1821.5, '2019-11-22'),
            action(2, 'DIVIDEND', 182.15, day='2020-06-12'),
            action(3, 'REDEEM', 1141.87, 607.17, '2020-09-08'),
            action(4, 'DIVIDEND', 799.03, day='2021-07-21'),
            action(5, 'BUY', 26720, 19142.76, '2023-12-20'),
            action(6, 'REDEEM', 1037.7, 500, '2026-04-13')]
    if pending:
        rows.append(action(7, 'ADD', pending, day='2026-11-18', status='pending'))

    async def actions(*_args, **_kwargs):
        return rows

    async def no_quote(_code):
        return None

    monkeypatch.setattr(assets_routes, 'list_external_actions', actions)
    monkeypatch.setattr(assets_routes, 'get_fund_quote', no_quote)
    result = asyncio.run(assets_routes._enrich({
        'id': 7, 'asset_type': 'FUND', 'code': '000711',
        'manual_value': 32863.48 + pending, 'cost_amount': 0, 'shares': 0,
    }))
    assert result['shares'] == 19857.09
    assert result['cost_amount'] == 27884.5312
    assert result['holding_dividend'] == 897.86
    assert result['pnl'] == 5876.81
    assert result['pnl_pct'] == 21.08
    assert round(result['pnl'] + result['realized_pnl'] - result['holding_dividend'], 2) == 6504.23
