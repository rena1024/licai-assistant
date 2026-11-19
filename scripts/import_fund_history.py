"""补录支付宝基金历史交易；默认预览，--apply 备份后写入。"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3
import sys
from zipfile import BadZipFile

from openpyxl.utils.exceptions import InvalidFileException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import config
from database import PORTFOLIO_SCHEMA_VERSION
from scripts.fund_history_format import NOTE_PREFIX, order_id, read_actions, signature
from services import backup
from services.external_ledger import compute_external_state
from services.runtime_paths import database_path, get_runtime_paths, RuntimePathError
from services.runtime_state import check_generation, runtime_scope, RuntimeUnavailable
from services.user_context import user_scope


HISTORY_NOTICE = '基金历史补录：未补齐期初及更早流水前，份额、成本、盈亏仅反映已录入记录。'


def imported_order(action):
    note = action.get('note') or ''
    if not note.startswith(NOTE_PREFIX):
        return None
    try:
        source = json.loads(note[len(NOTE_PREFIX):])
        order = source['order_id']
        if not isinstance(order, str) or not order:
            raise ValueError()
        return order
    except (ValueError, KeyError, TypeError):
        raise ValueError('已有基金流水的来源订单信息损坏，请先核对') from None


def select_new_actions(incoming, existing, code):
    orders = {}
    manual = []
    for action in existing:
        order = imported_order(action)
        if order is None:
            manual.append(action)
        elif order in orders:
            raise ValueError(f'{code} 已有重复来源订单 {order}，请先核对')
        else:
            orders[order] = action
    pending = []
    for action in incoming:
        order = action['order_id']
        if order in orders:
            if signature(orders[order]) != signature(action):
                raise ValueError(f'{code} 订单 {order} 与已有流水内容冲突，请核对后再导入')
            continue
        kind = action['action_type']
        if any(str(old.get('trade_date') or old.get('created_at') or '')[:10] == action['trade_date']
               and ('BUY' if old['action_type'] == 'ADD' else old['action_type']) == kind
               for old in manual):
            raise ValueError(f'{code} 在 {action["trade_date"]} 存在未带来源订单号的同类流水，请先核对，避免重复入账')
        pending.append(action)
    return pending


def share_shortfall(actions):
    """只报告缺口，不生成虚构的零成本期初仓位。"""
    balance, required = 0.0, 0.0
    for action in sorted(actions, key=lambda a: (a.get('trade_date') or str(a.get('created_at') or '')[:10],
                                                a.get('trade_time') or '00:00', a.get('id') or 0)):
        if (action.get('status') or 'confirmed') != 'confirmed':
            continue
        kind, shares = action['action_type'], float(action.get('shares') or 0)
        if kind in ('BUY', 'ADD'):
            balance += shares
        elif kind in ('REDEEM', 'SELL'):
            balance -= shares
            required = max(required, -balance)
        elif kind == 'SPLIT' and shares > 0:
            balance *= shares
    return required


def plan_import(db, batches, owner, platform):
    plan = []
    for code, incoming in batches.items():
        assets = db.execute(
            "SELECT * FROM external_assets WHERE user_id=? AND asset_type='FUND' AND code=? AND platform=?",
            (owner, code, platform),
        ).fetchall()
        if len(assets) > 1:
            raise ValueError(f'{code} 在 {platform} 有多个基金资产，须先确认归属')
        asset = dict(assets[0]) if assets else None
        existing = [dict(a) for a in db.execute(
            'SELECT * FROM external_asset_actions WHERE user_id=? AND asset_id=? ORDER BY trade_date,id',
            (owner, asset['id']),
        )] if asset else []
        if asset and not existing and (asset['shares'] or asset['cost_amount']):
            raise ValueError(f'{code} 已有持仓但没有历史流水，请先核对期初持仓，工具不会覆盖')
        pending = select_new_actions(incoming, existing, code)
        if pending and any(a.get('note') == 'initial (auto-migrated)' for a in existing):
            raise ValueError(f'{code} 存在由当前持仓自动迁移的期初快照，请先核对快照与真实历史，避免重复累计')
        if pending and asset and asset.get('closed'):
            raise ValueError(f'{code} 的基金资产已归档，请先确认并重新启用后再补录，避免流水不可见')
        # 为预览赋予与真正插入一致的顺序，后补旧流水仍由账本按交易日期重算。
        next_id = max((a['id'] for a in existing), default=0)
        combined = existing + [dict(a, id=next_id+i) for i, a in enumerate(pending, 1)]
        state = compute_external_state(combined, 'FUND')
        plan.append(dict(code=code, name=incoming[-1]['name'], incoming=incoming, pending=pending,
                         asset=asset, state=state, shortfall=share_shortfall(combined),
                         start_date=min(a['trade_date'] or str(a['created_at'])[:10] for a in combined)))
    return plan


def write_plan(db, plan, owner, platform):
    for item in plan:
        if not item['pending']:
            continue
        asset, state = item['asset'], item['state']
        if asset:
            asset_id = asset['id']
            note = asset.get('note') or ''
            if HISTORY_NOTICE not in note:
                note = (note + '\n' + HISTORY_NOTICE).strip()
            db.execute(
                'UPDATE external_assets SET shares=?,cost_amount=?,start_date=?,note=?,updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND id=?',
                (state['shares'], state['cost_amount'], item['start_date'], note, owner, asset_id),
            )
        else:
            cur = db.execute(
                "INSERT INTO external_assets (user_id,asset_type,code,name,platform,cost_amount,shares,start_date,note) VALUES (?,'FUND',?,?,?,?,?,?,?)",
                (owner, item['code'], item['name'], platform, state['cost_amount'], state['shares'],
                 item['start_date'], HISTORY_NOTICE),
            )
            asset_id = cur.lastrowid
        db.executemany(
            '''INSERT INTO external_asset_actions
               (user_id,asset_id,action_type,amount,shares,unit_price,fee,trade_date,trade_time,status,note)
               VALUES (:user_id,:asset_id,:action_type,:amount,:shares,:unit_price,:fee,:trade_date,:trade_time,:status,:note)''',
            [dict(a, user_id=owner, asset_id=asset_id) for a in item['pending']],
        )


def run_import(batches, owner, platform, apply=False):
    paths = get_runtime_paths()
    paths.check_legacy_database()
    target = database_path(config.db_path)
    if not target.is_file():
        raise ValueError('找不到已初始化的理财数据库，请检查 LICAI_DATA_DIR')
    paths.check_database(target)
    mode = 'rw' if apply else 'ro'
    with runtime_scope(), user_scope(owner), closing(sqlite3.connect(f'{target.as_uri()}?mode={mode}', uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        with db:
            db.execute('BEGIN IMMEDIATE' if apply else 'BEGIN')
            version = db.execute("SELECT value FROM runtime_metadata WHERE key='portfolio_schema_version'").fetchone()
            if version is None or version['value'] != PORTFOLIO_SCHEMA_VERSION:
                raise ValueError('基金账本版本不兼容')
            user = db.execute('SELECT username FROM users WHERE id=? AND active=1', (owner,)).fetchone()
            if user is None:
                raise ValueError(f'账号 ID {owner} 不存在或未启用')
            print(f'数据库 {target}\n账号 {user["username"]}（ID {owner}），平台 {platform}')
            print('注意：' + HISTORY_NOTICE)
            plan = plan_import(db, batches, owner, platform)
            for item in plan:
                counts = Counter(a['action_type'] for a in item['pending'])
                print(f'{item["code"]} {item["name"]}：新增 {len(item["pending"])} 笔'
                      f'（买入 {counts["BUY"]}，赎回 {counts["REDEEM"]}，现金分红 {counts["DIVIDEND"]}），'
                      f'跳过 {len(item["incoming"])-len(item["pending"])} 笔；'
                      f'已录账本份额 {item["state"]["shares"]:.6f}，剩余成本 ¥{item["state"]["cost_amount"]:.4f}')
                if item['shortfall'] > 1e-6:
                    print(f'  历史赎回存在至少 {item["shortfall"]:.6f} 份的期初/缺失流水缺口；'
                          '当前份额及盈亏不代表完整持仓，后补历史后重算。')
            total = sum(len(item['pending']) for item in plan)
            skipped = sum(len(item['incoming'])-len(item['pending']) for item in plan)
            print(f'汇总：{len(plan)} 只基金，预计新增 {total} 笔，已入账跳过 {skipped} 笔。')
            if not any(item['pending'] for item in plan):
                print('没有需要补录的流水。')
                return
            if not apply:
                print('预览完成，未写入数据库。加 --apply 备份并写入。')
                return
            point = backup.create_backup(kind='protective')
            print(f'导入前备份 {paths.backups / point["id"]}')
            write_plan(db, plan, owner, platform)
            check_generation()
        print('基金历史补录完成。后补更早历史时，会与已有流水一起重算。')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file', type=Path, help='基金交易明细 .xlsx 文件')
    parser.add_argument('--user-id', type=int, required=True, help='理财助手账号 ID')
    parser.add_argument('--sheet', default='position', help='明细工作表，默认 position')
    parser.add_argument('--platform', default='支付宝', help='持有平台，默认支付宝')
    parser.add_argument('--fund-code', help='仅导入指定六位基金代码')
    parser.add_argument('--exclude-order', action='append', default=[], help='明确排除的来源订单号，可重复指定')
    parser.add_argument('--apply', action='store_true', help='备份并写入，省略则只读预览')
    args = parser.parse_args(argv)
    try:
        platform = args.platform.strip()
        if not platform:
            raise ValueError('持有平台不能为空')
        if args.user_id <= 0:
            raise ValueError('账号 ID 须为正整数')
        if args.fund_code and not re.fullmatch(r'[0-9]{6}', args.fund_code):
            raise ValueError('--fund-code 须为六位基金代码')
        exclusions = sorted({order_id(order) for order in args.exclude_order})
        batches = read_actions(args.file, args.sheet, args.fund_code, exclusions)
        if exclusions:
            print(f'按参数明确排除 {len(exclusions)} 个订单：' + '、'.join(exclusions))
        run_import(batches, args.user_id, platform, args.apply)
    except (BadZipFile, InvalidFileException, OSError, ValueError, sqlite3.DatabaseError,
            backup.BackupError, RuntimePathError, RuntimeUnavailable) as exc:
        print(f'基金补录失败：{exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
