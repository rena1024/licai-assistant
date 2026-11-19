"""将券商交割明细补录至本机理财助手的股票账本。"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from contextlib import closing
from pathlib import Path
import re
import sqlite3
import sys
from zipfile import BadZipFile

from openpyxl.utils.exceptions import InvalidFileException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import config
from database import PORTFOLIO_SCHEMA_VERSION
from scripts.stock_history_format import load_mapping, read_actions
from services import backup
from services.position_ledger import ACQUIRE, RELEASE, SPLIT, ledger_state, split_ratio
from services.runtime_paths import database_path, get_runtime_paths, RuntimePathError
from services.runtime_state import RuntimeUnavailable, check_generation, runtime_scope
from services.user_context import user_scope

PORTFOLIO_ACTION_LIMIT = 500  # 持仓、交易历史和盈亏接口的单标的读取上限


def action_date(action):
    return (action.get("trade_date") or action.get("created_at") or "")[:10]


def trade_key(action, holding_broker):
    kind = action["action_type"]
    if kind == "ADD":
        kind = "BUY"
    elif kind == "REDUCE":
        kind = "SELL"
    price, shares = action["price"], action["shares"]
    if kind == "DIVIDEND":
        price, shares = round(price * shares, 2), 1
    return action_date(action), kind, price, shares, action["fee"], action["broker"] or holding_broker


def select_new_actions(incoming, existing, holding_broker):
    keys = Counter(trade_key(a, holding_broker) for a in existing)
    existing_days = {action_date(a) for a in existing}
    pending = []
    for action in incoming:
        key = trade_key(action, holding_broker)
        if keys[key]:
            keys[key] -= 1
        elif action_date(action) in existing_days:
            raise ValueError(f"{action['stock_code']} 在 {action['trade_date']} 有无法匹配的流水，请核对原有记录")
        else:
            pending.append(action)
    return pending


def check_share_history(code, actions):
    shares = 0
    for action in actions:
        kind = action["action_type"]
        if kind in ACQUIRE:
            shares += action["shares"]
        elif kind == SPLIT:
            ratio = split_ratio(action)
            if ratio > 0:
                shares = int(shares * ratio + 1e-6)
        elif kind in RELEASE:
            shares -= action["shares"]
            if shares < 0:
                raise ValueError(f"{code} 在 {action_date(action)} 卖出后持仓为负，请补齐期初持仓或缺失流水")


async def plan_import(db, batches, owner, broker):
    plan = []
    for code, incoming in batches.items():
        holding = db.execute("SELECT * FROM holdings WHERE user_id=? AND stock_code=?", (owner, code)).fetchone()
        existing = [dict(row) for row in db.execute(
            "SELECT * FROM position_actions WHERE user_id=? AND stock_code=? ORDER BY created_at DESC, id",
            (owner, code),
        )]
        holding_broker = holding["broker"] if holding else broker
        pending = select_new_actions(incoming, existing, holding_broker)
        state = None
        if pending:
            if holding and holding["shares"] and not existing:
                raise ValueError(f"{code} 已有持仓但没有建仓流水，请先核对期初持仓")
            combined = sorted([*existing, *pending], key=action_date)
            if len(combined) > PORTFOLIO_ACTION_LIMIT:
                raise ValueError(f"{code} 的总流水将超过页面支持的 {PORTFOLIO_ACTION_LIMIT} 笔")
            check_share_history(code, combined)
            state = await ledger_state(code, combined, holding_broker, dilute=False)
        plan.append((code, incoming, pending, holding, state))
    return plan


def write_plan(db, plan, owner, broker):
    for code, incoming, pending, holding, state in plan:
        if not pending:
            continue
        db.executemany("""
            INSERT INTO position_actions (user_id, stock_code, action_type, price, shares, trade_date, fee, broker, note)
            VALUES (:user_id, :stock_code, :action_type, :price, :shares, :trade_date, :fee, :broker, :note)
        """, [dict(action, user_id=owner) for action in pending])
        if holding:
            db.execute("""
                UPDATE holdings SET shares=?, cost_price=?, updated_at=CURRENT_TIMESTAMP
                WHERE user_id=? AND stock_code=?
            """, (state["shares"], state["cost_price"], owner, code))
        else:
            name = next((a["stock_name"] for a in incoming if a["stock_name"]), code)
            first_buy = min((a["trade_date"] for a in incoming if a["action_type"] == "BUY"), default=None)
            db.execute("""
                INSERT INTO holdings (user_id, stock_code, stock_name, shares, cost_price, purchase_date, broker)
                VALUES (?,?,?,?,?,?,?)
            """, (owner, code, name, state["shares"], state["cost_price"], first_buy, broker))


async def run_import(batches, owner, broker, apply):
    paths = get_runtime_paths()
    paths.check_legacy_database()
    target = database_path(config.db_path)
    if not target.is_file():
        raise ValueError("找不到已初始化的理财数据库，请检查 LICAI_DATA_DIR")
    paths.check_database(target)
    mode = "rw" if apply else "ro"
    with runtime_scope(), user_scope(owner), closing(sqlite3.connect(f"{target.as_uri()}?mode={mode}", uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        with db:
            db.execute("BEGIN IMMEDIATE" if apply else "BEGIN")
            version = db.execute("SELECT value FROM runtime_metadata WHERE key='portfolio_schema_version'").fetchone()
            if version is None or version["value"] != PORTFOLIO_SCHEMA_VERSION:
                raise ValueError("股票账本版本不兼容")
            user = db.execute("SELECT username FROM users WHERE id=? AND active=1", (owner,)).fetchone()
            if user is None:
                raise ValueError(f"账号 ID {owner} 不存在或未启用")
            print(f"数据库 {target}\n账号 {user['username']}（ID {owner}），券商 {broker}")
            plan = await plan_import(db, batches, owner, broker)
            for code, incoming, pending, _, state in plan:
                counts = Counter(a["action_type"] for a in pending)
                print(f"{code} 新增 {len(pending)} 笔（买入 {counts['BUY']}，卖出 {counts['SELL']}，股息 {counts['DIVIDEND']}），"
                      f"跳过 {len(incoming) - len(pending)} 笔")
                if state:
                    dividend = sum(a["price"] for a in pending if a["action_type"] == "DIVIDEND")
                    print(f"  补录后持仓 {state['shares']} 股，账本成本 ¥{state['cost_price']:.4f}/股")
                    if dividend:
                        print(f"  新增实收股息 ¥{dividend:.2f}")
            if not any(pending for _, _, pending, _, _ in plan):
                print("没有需要补录的流水。")
                return
            if not apply:
                print("预览完成。加 --apply 写入数据库。")
                return
            point = backup.create_backup(kind="protective")
            print(f"导入前备份 {paths.backups / point['id']}")
            write_plan(db, plan, owner, broker)
            check_generation()
        print("补录完成。")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path, help="券商交割明细 .xlsx 文件")
    parser.add_argument("--user-id", required=True, type=int, help="理财助手账号 ID")
    parser.add_argument("--broker", help="覆盖映射中的券商名称")
    parser.add_argument("--mapping", type=Path, help="券商格式映射 JSON，省略时使用华泰证券内置映射")
    parser.add_argument("--stock-code", help="只处理指定股票，例如 600009")
    parser.add_argument("--apply", action="store_true", help="备份并写入数据库，省略时只预览")
    args = parser.parse_args(argv)
    try:
        mapping = load_mapping(args.mapping)
        broker = (args.broker if args.broker is not None else mapping.get("broker", "")).strip()
        if not broker:
            raise ValueError("请在映射中设置 broker 或通过 --broker 指定券商")
        if args.stock_code and not re.fullmatch(r"[036][0-9]{5}", args.stock_code):
            raise ValueError("--stock-code 须为 6 位沪深 A 股证券代码")
        batches, ignored = read_actions(args.file, broker, args.stock_code, mapping)
        if ignored:
            print(f"按映射忽略 {ignored} 笔非导入业务。")
        if any(a["fee"] is None for batch in batches.values() for a in batch):
            print("配置未提供费用列，交易费用由账本估算。")
        asyncio.run(run_import(batches, args.user_id, broker, args.apply))
    except (BadZipFile, InvalidFileException, OSError, ValueError, sqlite3.DatabaseError,
            backup.BackupError, RuntimePathError, RuntimeUnavailable) as exc:
        print(f"补录失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
