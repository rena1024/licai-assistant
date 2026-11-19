import asyncio
import json
import sqlite3
from datetime import datetime

import pytest
from openpyxl import Workbook, load_workbook

from config import config
from database import init_db
from services.external_ledger import compute_external_state


HEADERS = ['订单号', '交易时间', '交易类型', '基金名称', '组合基金名称', '基金代码',
           '申请金额', '申请份额', '确认金额', '确认份额', '手续费', '确认日期']


def record(order='000000000000000000000001', kind='用户买入', amount=100, shares=50,
           fee=1, code=711, day='2026/01/02 09:00'):
    return [order, day, kind, '样例基金', None, code, amount, '/', amount, shares, fee,
            day.split()[0] + ' 00:00']


def workbook(tmp_path, rows):
    book = Workbook()
    book.active.title = '其他工作表'
    sheet = book.create_sheet('position')
    sheet.append(HEADERS)
    for row in rows:
        sheet.append(row)
    path = tmp_path / 'funds.xlsx'
    book.save(path)
    return path


def setup_db():
    asyncio.run(init_db())
    with sqlite3.connect(config.db_path) as db:
        db.executemany(
            "INSERT INTO users (id, uuid, username, password_hash, role, created_at) VALUES (?, ?, ?, 'unused', 'user', 0)",
            [(1, 'owner', 'owner'), (2, 'other', 'other')],
        )


def test_parser_maps_all_business_rules_and_preserves_source(tmp_path):
    from scripts.fund_history_format import read_actions

    kinds = ['定投买入', '用户买入', '用户跨 TA转换', '用户转换', '营销买入', '用户卖出', '机构分红']
    path = workbook(tmp_path, [record(order=str(i), kind=k, fee=0 if k in kinds[4:] else 1,
                                      shares=0 if k == '机构分红' else 50)
                               for i, k in enumerate(kinds, 1)])
    batches = read_actions(path)
    actions = batches['000711']
    assert len(actions) == 7
    assert [a['action_type'] for a in actions] == ['BUY'] * 5 + ['REDEEM', 'DIVIDEND']
    buy, gift, sale, dividend = actions[0], actions[4], actions[5], actions[6]
    assert (buy['amount'], buy['shares'], buy['fee'], buy['unit_price']) == (100, 50, 1, 1.98)
    assert (gift['amount'], gift['shares'], gift['fee'], gift['unit_price']) == (0, 50, 0, 0)
    assert (sale['amount'], sale['fee'], dividend['shares']) == (100, 0, None)
    assert json.loads(buy['note'].split('|', 1)[1])['confirmation_date'] == '2026-01-02'
    assert buy['trade_date'] == '2026-01-02' and buy['trade_time'] == '09:00'


@pytest.mark.parametrize('column,value', [
    (2, '未知业务'), (5, 'abc'), (8, -1), (9, 0), (10, 101), (1, '不合法日期'),
    (0, 12345678901234567890), (8, float('inf')),
])
def test_parser_rejects_invalid_rows_with_row_number(tmp_path, column, value):
    from scripts.fund_history_format import read_actions

    row = record()
    row[column] = value
    path = workbook(tmp_path, [row])
    with pytest.raises(ValueError, match='第 2 行'):
        read_actions(path)


def test_parser_duplicate_orders_blank_rows_and_date_objects(tmp_path):
    from scripts.fund_history_format import read_actions

    row = record()
    row[1], row[11] = datetime(2026, 1, 2, 9), datetime(2026, 1, 3)
    path = workbook(tmp_path, [row, [None] * 12, row])
    assert len(read_actions(path)['000711']) == 1
    book = load_workbook(path)
    book['position']['I4'] = 101
    book.save(path)
    with pytest.raises(ValueError, match='订单.*冲突'):
        read_actions(path)


def test_fee_inclusive_cost_survives_partial_and_full_redemption():
    buy = dict(id=1, action_type='BUY', amount=101, shares=100, fee=1,
               unit_price=1, trade_date='2026-01-02')
    sale = dict(id=2, action_type='REDEEM', amount=60, shares=50, unit_price=1.2,
                fee=0, trade_date='2026-01-03')
    partial = compute_external_state([buy, sale], 'FUND')
    assert partial['cost_amount'] == 50.5
    assert partial['realized_pnl'] == 9.5
    full = compute_external_state([buy, dict(sale, amount=120, shares=100)], 'FUND')
    assert full['cost_amount'] == 0
    assert full['realized_pnl'] == 19


def test_preview_apply_repeat_and_owner_isolation(tmp_path, monkeypatch, capsys):
    from scripts.import_fund_history import main

    setup_db()
    path = workbook(tmp_path, [record(), record(order='gift', kind='营销买入', shares=5, fee=0),
                               record(order='div', kind='机构分红', amount=3, shares=0, fee=0)])
    calls = []
    monkeypatch.setattr('scripts.import_fund_history.backup.create_backup',
                        lambda **kw: calls.append(kw) or {'id': 'test-backup'})
    args = [str(path), '--user-id', '1']
    assert main(args) == 0
    with sqlite3.connect(config.db_path) as db:
        assert db.execute('SELECT COUNT(*) FROM external_assets').fetchone()[0] == 0
    assert calls == []
    assert main([*args, '--apply']) == 0
    with sqlite3.connect(config.db_path) as db:
        assert db.execute('SELECT code, platform, shares, cost_amount FROM external_assets').fetchall() == [
            ('000711', '支付宝', 55, 100)]
        assert db.execute('SELECT COUNT(*) FROM external_asset_actions').fetchone()[0] == 3
        assert db.execute('SELECT COUNT(*) FROM position_actions').fetchone()[0] == 0
    assert len(calls) == 1
    assert main([*args, '--apply']) == 0
    assert len(calls) == 1
    assert main([str(path), '--user-id', '2', '--apply']) == 0
    with sqlite3.connect(config.db_path) as db:
        assert db.execute('SELECT user_id, COUNT(*) FROM external_asset_actions GROUP BY user_id').fetchall() == [(1, 3), (2, 3)]
    assert '期初' in capsys.readouterr().out


def test_existing_order_conflict_rolls_back_all_funds(tmp_path, monkeypatch):
    from scripts.import_fund_history import main

    setup_db()
    monkeypatch.setattr('scripts.import_fund_history.backup.create_backup', lambda **kw: {'id': 'test'})
    path = workbook(tmp_path, [record()])
    args = [str(path), '--user-id', '1', '--apply']
    assert main(args) == 0
    path = workbook(tmp_path, [record(order='another', code=12314), record(amount=101)])
    assert main(args) == 1
    with sqlite3.connect(config.db_path) as db:
        assert db.execute('SELECT COUNT(*) FROM external_assets').fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM external_asset_actions').fetchone()[0] == 1


def test_incomplete_history_can_be_imported_and_earlier_history_added(tmp_path, monkeypatch, capsys):
    from scripts.import_fund_history import main

    setup_db()
    monkeypatch.setattr('scripts.import_fund_history.backup.create_backup', lambda **kw: {'id': 'test'})
    path = workbook(tmp_path, [record(order='sale', kind='用户卖出', amount=60, shares=50, fee=0)])
    args = [str(path), '--user-id', '1', '--apply']
    assert main(args) == 0
    assert '期初' in capsys.readouterr().out
    path = workbook(tmp_path, [record(day='2021/01/02 09:00', amount=100, shares=100, fee=0)])
    assert main(args) == 0
    with sqlite3.connect(config.db_path) as db:
        assert db.execute('SELECT shares, cost_amount FROM external_assets').fetchone() == (50, 50)
        assert db.execute('SELECT COUNT(*) FROM external_asset_actions').fetchone()[0] == 2


def test_manual_history_and_duplicate_assets_are_not_overwritten(tmp_path, monkeypatch):
    from scripts.import_fund_history import main

    setup_db()
    with sqlite3.connect(config.db_path) as db:
        db.execute("INSERT INTO external_assets (user_id,asset_type,code,name,platform,cost_amount,shares) VALUES (1,'FUND','000711','人工基金','支付宝',500,50)")
    path = workbook(tmp_path, [record()])
    assert main([str(path), '--user-id', '1', '--apply']) == 1
    with sqlite3.connect(config.db_path) as db:
        assert db.execute('SELECT cost_amount FROM external_assets').fetchone()[0] == 500


def test_backup_failure_prevents_any_writes(tmp_path, monkeypatch):
    from scripts.import_fund_history import main
    from services.backup import BackupError

    setup_db()
    def fail(**kwargs):
        raise BackupError()
    monkeypatch.setattr('scripts.import_fund_history.backup.create_backup', fail)
    path = workbook(tmp_path, [record()])
    assert main([str(path), '--user-id', '1', '--apply']) == 1
    with sqlite3.connect(config.db_path) as db:
        assert db.execute('SELECT COUNT(*) FROM external_assets').fetchone()[0] == 0


def test_real_backup_contains_pre_import_state(tmp_path):
    from scripts.import_fund_history import main
    from services.runtime_paths import get_runtime_paths

    setup_db()
    backups = get_runtime_paths().backups
    previous = set(backups.glob('protective-*'))
    path = workbook(tmp_path, [record()])
    assert main([str(path), '--user-id', '1', '--apply']) == 0
    created = set(backups.glob('protective-*')) - previous
    assert len(created) == 1
    with sqlite3.connect((created.pop() / 'database.sqlite3').as_posix()) as db:
        assert db.execute('SELECT COUNT(*) FROM external_assets').fetchone()[0] == 0


def test_database_write_failure_rolls_back_all_changes(tmp_path, monkeypatch):
    from scripts.import_fund_history import main

    setup_db()
    monkeypatch.setattr('scripts.import_fund_history.backup.create_backup', lambda **kw: {'id': 'test'})
    with sqlite3.connect(config.db_path) as db:
        db.execute("CREATE TRIGGER fail_second BEFORE INSERT ON external_asset_actions WHEN NEW.amount=101 BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END")
    path = workbook(tmp_path, [record(), record(order='second', code=12314, amount=101)])
    assert main([str(path), '--user-id', '1', '--apply']) == 1
    with sqlite3.connect(config.db_path) as db:
        assert db.execute('SELECT COUNT(*) FROM external_assets').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM external_asset_actions').fetchone()[0] == 0


def test_cash_dividend_with_positive_shares_is_rejected(tmp_path):
    from scripts.fund_history_format import read_actions

    path = workbook(tmp_path, [record(kind='机构分红', amount=0, shares=0.4, fee=0)])
    with pytest.raises(ValueError, match='现金分红'):
        read_actions(path)


def test_explicit_order_exclusion_skips_only_requested_order(tmp_path):
    from scripts.fund_history_format import read_actions

    path = workbook(tmp_path, [record(), record(order='exception', kind='机构分红', amount=0, shares=0.4, fee=0)])
    batches = read_actions(path, excluded_orders=['exception'])
    assert len(batches['000711']) == 1
    assert batches['000711'][0]['order_id'] == record()[0]
    with pytest.raises(ValueError, match='排除订单.*不存在'):
        read_actions(path, excluded_orders=['exception', 'typo'])


def test_backfilled_same_day_trade_uses_time_instead_of_import_order(tmp_path, monkeypatch):
    from scripts.import_fund_history import main

    setup_db()
    monkeypatch.setattr('scripts.import_fund_history.backup.create_backup', lambda **kw: {'id': 'test'})
    path = workbook(tmp_path, [record(order='initial', amount=50, shares=50, fee=0, day='2026/01/01 09:00'),
                               record(order='sale', kind='用户卖出', amount=120, shares=100, fee=0, day='2026/01/02 15:00')])
    args = [str(path), '--user-id', '1', '--apply']
    assert main(args) == 0
    path = workbook(tmp_path, [record(order='earlier', amount=200, shares=100, fee=0, day='2026/01/02 09:00')])
    assert main(args) == 0
    with sqlite3.connect(config.db_path) as db:
        db.row_factory = sqlite3.Row
        actions = [dict(row) for row in db.execute('SELECT * FROM external_asset_actions')]
    state = compute_external_state(actions, 'FUND')
    # 上午先加仓，平均成本为 250 / 150；下午赎回 100 份，成本结转 166.6667。
    assert state['shares'] == 50
    assert state['cycle_realized'] == -46.67
    assert state['cost_amount'] == 83.3333
    assert state['diluted_cost'] == 130


def test_import_cannot_silently_add_to_archived_asset(tmp_path):
    from scripts.import_fund_history import main

    setup_db()
    with sqlite3.connect(config.db_path) as db:
        db.execute("INSERT INTO external_assets (user_id,asset_type,code,name,platform,cost_amount,shares,closed) VALUES (1,'FUND','000711','已归档基金','支付宝',0,0,1)")
    path = workbook(tmp_path, [record()])
    assert main([str(path), '--user-id', '1', '--apply']) == 1


def test_cli_reports_excluded_order_without_importing_it(tmp_path, monkeypatch, capsys):
    from scripts.import_fund_history import main

    setup_db()
    monkeypatch.setattr('scripts.import_fund_history.backup.create_backup', lambda **kw: {'id': 'test'})
    path = workbook(tmp_path, [record(), record(order='exception', kind='机构分红', amount=0, shares=0.4, fee=0)])
    assert main([str(path), '--user-id', '1', '--exclude-order', 'exception', '--apply']) == 0
    assert '明确排除 1 个订单' in capsys.readouterr().out
    with sqlite3.connect(config.db_path) as db:
        assert db.execute('SELECT COUNT(*) FROM external_asset_actions').fetchone()[0] == 1


def test_missing_sheet_or_duplicate_headers_are_rejected(tmp_path):
    from scripts.fund_history_format import read_actions

    path = workbook(tmp_path, [record()])
    with pytest.raises(ValueError, match='找不到工作表'):
        read_actions(path, sheet='不存在')
    book = load_workbook(path)
    book['position']['E1'] = '基金代码'
    book.save(path)
    with pytest.raises(ValueError, match='表头缺失或重复'):
        read_actions(path)


def test_multiple_assets_same_code_and_platform_are_rejected(tmp_path):
    from scripts.import_fund_history import main

    setup_db()
    with sqlite3.connect(config.db_path) as db:
        db.executemany("INSERT INTO external_assets (user_id,asset_type,code,name,platform,cost_amount,shares) VALUES (1,'FUND','000711',?,'支付宝',0,0)", [('资产一',), ('资产二',)])
    path = workbook(tmp_path, [record()])
    assert main([str(path), '--user-id', '1']) == 1


def test_order_reordering_does_not_duplicate_and_fund_filter_works(tmp_path, monkeypatch):
    from scripts.import_fund_history import main

    setup_db()
    monkeypatch.setattr('scripts.import_fund_history.backup.create_backup', lambda **kw: {'id': 'test'})
    first, second = record(), record(order='second', day='2026/01/03 09:00')
    path = workbook(tmp_path, [second, first, record(order='other', code=12314)])
    args = [str(path), '--user-id', '1', '--fund-code', '000711', '--apply']
    assert main(args) == 0
    path = workbook(tmp_path, [first, second])
    assert main(args) == 0
    with sqlite3.connect(config.db_path) as db:
        assert db.execute('SELECT code FROM external_assets').fetchall() == [('000711',)]
        assert db.execute('SELECT COUNT(*) FROM external_asset_actions').fetchone()[0] == 2


def test_same_day_manual_history_is_not_duplicated(tmp_path):
    from scripts.import_fund_history import main

    setup_db()
    with sqlite3.connect(config.db_path) as db:
        cur = db.execute("INSERT INTO external_assets (user_id,asset_type,code,name,platform,cost_amount,shares) VALUES (1,'FUND','000711','人工基金','支付宝',100,50)")
        db.execute("INSERT INTO external_asset_actions (user_id,asset_id,action_type,amount,shares,unit_price,trade_date) VALUES (1,?,'ADD',100,50,2,'2026-01-02')", (cur.lastrowid,))
    path = workbook(tmp_path, [record()])
    assert main([str(path), '--user-id', '1', '--apply']) == 1


def test_auto_migrated_snapshot_cannot_be_counted_as_real_history(tmp_path):
    from scripts.import_fund_history import main

    setup_db()
    with sqlite3.connect(config.db_path) as db:
        cur = db.execute("INSERT INTO external_assets (user_id,asset_type,code,name,platform,cost_amount,shares) VALUES (1,'FUND','000711','原持仓','支付宝',100,50)")
        db.execute("INSERT INTO external_asset_actions (user_id,asset_id,action_type,amount,shares,unit_price,trade_date,note) VALUES (1,?,'BUY',100,50,2,'2026-01-01','initial (auto-migrated)')", (cur.lastrowid,))
    path = workbook(tmp_path, [record(fee=0)])
    assert main([str(path), '--user-id', '1', '--apply']) == 1
    with sqlite3.connect(config.db_path) as db:
        assert db.execute('SELECT shares, cost_amount FROM external_assets').fetchone() == (50, 100)
        assert db.execute('SELECT COUNT(*) FROM external_asset_actions').fetchone()[0] == 1


@pytest.mark.parametrize('owner', ['-1', '999'])
def test_invalid_or_unknown_owner_is_rejected(tmp_path, owner):
    from scripts.import_fund_history import main

    setup_db()
    path = workbook(tmp_path, [record()])
    assert main([str(path), '--user-id', owner, '--apply']) == 1
