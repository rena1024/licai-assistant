import asyncio
import json
import sqlite3

import pytest
from openpyxl import Workbook

from config import config
from database import init_db
from scripts.import_stock_history import main


def test_imported_cash_dividend_is_counted_once_in_portfolio(tmp_path, monkeypatch):
    from api import portfolio_routes as routes
    from database import add_position_action

    asyncio.run(init_db())
    with sqlite3.connect(config.db_path) as db:
        db.execute(
            "INSERT INTO users (id, uuid, username, password_hash, role, created_at) VALUES (1, 'owner-uuid', 'owner', 'unused', 'user', 0)"
        )

    file = tmp_path / "dividends.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append("日期 币种 股东账号 证券代码 证券名称 摘要 成交数量 成交均价 佣金 印花税 其他费 发生金额 资金余额".split())
    sheet.append([20260102, "人民币", "账户A", "601727", "样例", "证券买入", 300, 8.8, 5, 0, 0.03, -2645.03, 0])
    sheet.append([20260103, "人民币", "账户A", "601727", "样例", "证券买入", 300, 7.92, 5, 0, 0.02, -2381.02, 0])
    sheet.append([20260104, "人民币", "账户A", "601727", "样例", "证券买入", 300, 7.43, 5, 0, 0.02, -2234.02, 0])
    sheet.append([20260106, "人民币", "账户A", "601727", "样例", "股息入帐", 0, 0, 0, 0, 0, 12.83, 0])
    sheet.append([20260107, "人民币", "账户A", "601727", "样例", "证券买入", 500, 6.2, 5, 0, 0.03, -3105.03, 0])
    workbook.save(file)
    assert main([str(file), "--user-id", "1", "--apply"]) == 0

    async def quotes(codes):
        return {code: {"price": 5.83, "change_pct": 0} for code in codes}

    async def sector(_code):
        return None

    public_requests = []

    def public_dividends(**kwargs):
        public_requests.append(kwargs)

    monkeypatch.setattr(routes, "get_realtime_quotes", quotes)
    monkeypatch.setattr(routes, "get_stock_sector", sector)
    monkeypatch.setattr("akshare.stock_fhps_detail_em", public_dividends)

    async def check():
        holding, = await routes.list_holdings()
        realized = await routes.realized_pnl()
        assert holding.shares == 1400
        assert holding.cost_value == 10352.27
        assert holding.original_cost_value == 10352.27
        assert holding.cost_price == 7.3945
        assert holding.unrealized_pnl == -2190.27
        assert holding.pnl_pct == -21.16
        assert realized["total_realized_pnl"] == 12.83
        assert realized["total_realized_carry"] == 0

        # 清仓后重新买入，旧周期的股息应保留在历史盈亏。
        await add_position_action("601727", "SELL", 5.83, 1400, "2026-01-08", fee=0, user_id=1)
        await add_position_action("601727", "BUY", 5.83, 100, "2026-01-09", fee=0, user_id=1)
        holding, = await routes.list_holdings()
        realized = await routes.realized_pnl()
        assert holding.cost_value == pytest.approx(583)
        assert holding.unrealized_pnl == 0
        assert realized["total_realized_carry"] == -2190.27

    asyncio.run(check())
    assert public_requests == []


def test_import_preview_apply_repeat_and_conflict(tmp_path):
    asyncio.run(init_db())
    with sqlite3.connect(config.db_path) as db:
        db.executemany(
            "INSERT INTO users (id, uuid, username, password_hash, role, created_at) VALUES (?, ?, ?, 'unused', 'user', 0)",
            [(1, "owner-uuid", "owner"), (2, "other-uuid", "other")],
        )

    file = tmp_path / "trades.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append("日期 币种 股东账号 证券代码 证券名称 摘要 成交数量 成交均价 佣金 印花税 其他费 发生金额 资金余额".split())
    sheet.append([20260102, "人民币", "账户A", "000001", "样例", "证券买入", 100, 10, 5, 0, 0, -1005, 1000])
    sheet.append([20260105, "人民币", "账户A", "000001", "样例", "证券卖出", -40, 12, 5, 0.24, 0, 474.76, 1474.76])
    sheet.append([20260106, "人民币", "账户A", "000001", "样例", "股息入帐", 0, 12, 0, 0, 0, 18.12, 1492.88])
    workbook.save(file)

    args = [str(file), "--user-id", "1", "--broker", "华泰证券"]
    with sqlite3.connect(config.db_path) as db:
        assert main(args) == 0
        assert db.execute("SELECT COUNT(*) FROM position_actions").fetchone() == (0,)
        assert main([*args, "--apply"]) == 0
        assert db.execute(
            "SELECT user_id, stock_code, shares, cost_price, broker FROM holdings"
        ).fetchall() == [(1, "000001", 60, 8.8373, "华泰证券")]
        assert db.execute(
            "SELECT action_type, shares, price, fee FROM position_actions ORDER BY id"
        ).fetchall() == [("BUY", 100, 10, 5), ("SELL", 40, 12, 5.24), ("DIVIDEND", 1, 18.12, 0)]
        assert main([*args, "--apply"]) == 0

        sheet.append([20260102, "人民币", "账户B", 2, "样例乙", "证券买入", 100, 10, 5, 0, 0, -1005, 1000])
        workbook.save(file)
        assert main([*args, "--stock-code", "000001", "--apply"]) == 0
        assert db.execute("SELECT COUNT(*) FROM holdings WHERE stock_code='000002'").fetchone() == (0,)

        sheet["H2"], sheet["L2"] = 11, -1105
        workbook.save(file)
        assert main([*args, "--apply"]) != 0
        assert db.execute("SELECT COUNT(*) FROM position_actions").fetchone() == (3,)

        sheet["A2"] = 20260107
        workbook.save(file)
        assert main([str(file), "--user-id", "2", "--apply"]) != 0
        assert db.execute("SELECT COUNT(*) FROM holdings WHERE user_id=2").fetchone() == (0,)


def test_custom_broker_mapping(tmp_path, capsys):
    asyncio.run(init_db())
    with sqlite3.connect(config.db_path) as db:
        db.execute(
            "INSERT INTO users (id, uuid, username, password_hash, role, created_at) VALUES (1, 'owner-uuid', 'owner', 'unused', 'user', 0)"
        )
    workbook = Workbook()
    sheet = workbook.create_sheet("成交明细")
    sheet.append(["交易对账单"])
    sheet.append(["业务", "成交日", "市场代码", "数量", "单价", "合计费用", "收付净额", "实收红利"])
    sheet.append(["B", "2026/1/2", 1, 100, 10, 5, 1005, None])
    sheet.append(["S", "2026/1/5", 1, 40, 12, 5.24, 474.76, None])
    sheet.append(["D", "2026/1/6", 1, None, None, None, None, 18.12])
    sheet.append(["IGNORE", None, None, None, None, None, None, None])
    file = tmp_path / "custom.xlsx"
    workbook.save(file)
    profile = tmp_path / "mapping.json"
    settings = {
        "broker": "样例证券",
        "sheet": "成交明细",
        "header_row": 2,
        "date_format": "%Y/%m/%d",
        "currency": "CNY",
        "settlement_mode": "absolute",
        "columns": {
            "action": "业务", "date": "成交日", "code": "市场代码", "quantity": "数量",
            "price": "单价", "settlement": "收付净额", "dividend_amount": "实收红利",
        },
        "fee_columns": ["合计费用"],
        "actions": {"B": "BUY", "S": "SELL", "D": "DIVIDEND", "IGNORE": "IGNORE"},
    }
    profile.write_text(json.dumps(settings), encoding="utf-8")
    args = [str(file), "--user-id", "1", "--mapping", str(profile)]
    with sqlite3.connect(config.db_path) as db:
        assert main(args) == 0
        assert db.execute("SELECT COUNT(*) FROM position_actions").fetchone() == (0,)
        assert main([*args, "--apply"]) == 0
        assert db.execute("SELECT action_type, fee, broker FROM position_actions ORDER BY id").fetchall() == [
            ("BUY", 5, "样例证券"), ("SELL", 5.24, "样例证券"), ("DIVIDEND", 0, "样例证券"),
        ]
        assert main([*args, "--apply"]) == 0
        assert db.execute("SELECT COUNT(*) FROM position_actions").fetchone() == (3,)
        sheet.append(["UNKNOWN", "2026/1/7", 1, 1, 10])
        workbook.save(file)
        assert main([*args, "--apply"]) != 0
        assert "Excel 第 7 行" in capsys.readouterr().err
        assert db.execute("SELECT COUNT(*) FROM position_actions").fetchone() == (3,)
