"""把不同券商的 Excel 列和业务摘要转换成统一的账本字段。"""
from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal, DecimalException, ROUND_HALF_UP
import json
import re

from openpyxl import load_workbook

CENT = Decimal("0.01")
CNY_NAMES = {"人民币", "CNY", "RMB"}
ACTION_TYPES = {"BUY", "SELL", "DIVIDEND", "IGNORE"}
COLUMN_KEYS = {"action", "date", "code", "name", "quantity", "price", "settlement",
               "dividend_amount", "account", "currency"}
MAPPING_KEYS = {"broker", "sheet", "header_row", "date_format", "currency",
                "settlement_mode", "columns", "fee_columns", "actions"}
HUATAI = {
    "broker": "华泰证券",
    "columns": {
        "action": "摘要", "date": "日期", "code": "证券代码", "name": "证券名称",
        "quantity": "成交数量", "price": "成交均价", "settlement": "发生金额",
        "account": "股东账号", "currency": "币种",
    },
    "fee_columns": ["佣金", "印花税", "其他费"],
    "actions": {"证券买入": "BUY", "证券卖出": "SELL", "股息入帐": "DIVIDEND", "股息入账": "DIVIDEND"},
}


def nonblank(value):
    return isinstance(value, str) and bool(value.strip())


def load_mapping(path):
    if path is None:
        return HUATAI
    mapping = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(mapping, dict) or mapping.keys() - MAPPING_KEYS:
        raise ValueError("映射必须是 JSON 对象，且只包含受支持的配置项")
    columns, actions = mapping.get("columns"), mapping.get("actions")
    if (not isinstance(columns, dict) or columns.keys() - COLUMN_KEYS
            or not {"action", "date", "code"} <= columns.keys()
            or not all(map(nonblank, columns.values()))):
        raise ValueError("columns 须配置 action、date、code 等受支持字段以及对应的非空列名")
    if (not isinstance(actions, dict) or not actions or not all(map(nonblank, actions))
            or any(not isinstance(kind, str) or kind not in ACTION_TYPES for kind in actions.values())):
        raise ValueError("actions 须将业务摘要映射到 BUY、SELL、DIVIDEND 或 IGNORE")
    kinds = set(actions.values())
    if kinds & {"BUY", "SELL"} and not {"quantity", "price"} <= columns.keys():
        raise ValueError("导入买卖须配置 quantity 和 price 列")
    if "DIVIDEND" in kinds and not {"dividend_amount", "settlement"} & columns.keys():
        raise ValueError("导入股息须配置 dividend_amount 或 settlement 列")
    fees = mapping.get("fee_columns", [])
    if not isinstance(fees, list) or not all(map(nonblank, fees)) or len(set(fees)) != len(fees):
        raise ValueError("fee_columns 须为不重复的列名列表")
    if "currency" in columns:
        if "currency" in mapping:
            raise ValueError("币种列和固定币种只能选一种")
    elif mapping.get("currency") != "CNY":
        raise ValueError("请配置币种列，或用 currency: CNY 声明文件全部为人民币")
    header = mapping.get("header_row", 1)
    if type(header) is not int or header < 1:
        raise ValueError("header_row 须为从 1 开始的表头行号")
    if any(key in mapping and not nonblank(mapping[key]) for key in ("broker", "sheet", "date_format")):
        raise ValueError("broker、sheet 和 date_format 须为非空文本")
    if mapping.get("settlement_mode", "signed") not in ("signed", "absolute"):
        raise ValueError("settlement_mode 须为 signed 或 absolute")
    return mapping


def parse_date(value, date_format):
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raw = str(value).strip()
    if date_format or re.fullmatch(r"[0-9]{8}", raw):
        return datetime.strptime(raw, date_format or "%Y%m%d").date().isoformat()
    return date.fromisoformat(raw).isoformat()


def number(value):
    result = Decimal(str(value).strip().replace(",", ""))
    if not result.is_finite():
        raise ValueError("数值必须是有限数字")
    return result


def settlement_check(fields, price, shares, fee, action_type, absolute_amount):
    amount = number(fields["settlement"])
    if amount != amount.quantize(CENT):
        raise ValueError("结算金额应精确到分")
    if fee is not None:
        gross = (price * shares).quantize(CENT, rounding=ROUND_HALF_UP)
        expected = -gross - fee if action_type == "BUY" else gross - fee
        if absolute_amount:
            expected = abs(expected)
        if expected != amount:
            raise ValueError(f"算出的结算金额为 {expected}，与表中金额 {amount} 不符")
    return amount


def parse_action(fields, fees, row, broker, code, action_type, mapping):
    currency = str(fields.get("currency", mapping.get("currency"))).strip().upper()
    if currency not in CNY_NAMES or not re.fullmatch(r"[036][0-9]{5}", code):
        raise ValueError("仅支持人民币沪深 A 股证券代码")
    fee_amounts = [number(value) if value not in (None, "") else Decimal(0) for value in fees]
    if any(value < 0 or value != value.quantize(CENT) for value in fee_amounts):
        raise ValueError("各项费用应为精确到分的非负金额")
    fee = sum(fee_amounts) if fee_amounts else None
    note = f"{broker}对账单补录｜Excel 第 {row} 行"
    if action_type == "DIVIDEND":
        amount = number(fields.get("dividend_amount", fields.get("settlement")))
        if fee or amount <= 0 or amount != amount.quantize(CENT):
            raise ValueError("股息须为精确到分的正数实收金额，且没有独立费用")
        price, shares, fee = amount, 1, Decimal(0)
        note += f"｜实收股息 ¥{amount:.2f}，按 1 笔记账，数量 1 仅用于表示总额"
    else:
        quantity = number(fields["quantity"])
        shares, price = abs(quantity), number(fields["price"])
        if not shares or shares != int(shares) or price <= 0 or (action_type == "BUY" and quantity < 0):
            raise ValueError("买入股数须为正整数，卖出股数须为非零整数，成交价须为正数")
        if "settlement" in fields:
            amount = settlement_check(fields, price, shares, fee, action_type, mapping.get("settlement_mode") == "absolute")
            note += f"｜结算 ¥{amount:.2f}"
    return dict(stock_code=code, stock_name=str(fields.get("name") or "").strip(), action_type=action_type,
                price=float(price), shares=int(shares), trade_date=parse_date(fields["date"], mapping.get("date_format")),
                fee=float(fee) if fee is not None else None, broker=broker, note=note)


def column_indices(header, mapping):
    names = [str(value or "").strip() for value in header]
    counts = Counter(names)
    requested = {*mapping["columns"].values(), *mapping.get("fee_columns", [])}
    missing = sorted(name for name in requested if not counts[name])
    duplicates = sorted(name for name in requested if counts[name] > 1)
    if missing or duplicates:
        details = [f"缺少列 {', '.join(missing)}"] if missing else []
        if duplicates:
            details.append(f"重复列 {', '.join(duplicates)}")
        raise ValueError("表头无法映射：" + "；".join(details))
    return ({key: names.index(name) for key, name in mapping["columns"].items()},
            [names.index(name) for name in mapping.get("fee_columns", [])])


def read_actions(path, broker, selected_code, mapping):
    book = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet_name = mapping.get("sheet")
        if sheet_name and sheet_name not in {sheet.title for sheet in book.worksheets}:
            raise ValueError(f"找不到工作表 {sheet_name!r}")
        if not sheet_name and len(book.worksheets) != 1:
            raise ValueError("文件有多张工作表，请用映射的 sheet 指定明细表")
        sheet = book[sheet_name] if sheet_name else book.worksheets[0]
        header_row = mapping.get("header_row", 1)
        rows = sheet.iter_rows(min_row=header_row, values_only=True)
        columns, fee_columns = column_indices(next(rows, ()), mapping)
        accounts, actions = {}, defaultdict(list)
        ignored = 0
        for row, values in enumerate(rows, header_row + 1):
            if not any(value not in (None, "") for value in values):
                continue
            fields = {field: values[index] for field, index in columns.items()}
            code = str(fields["code"]).strip()
            if re.fullmatch(r"[0-9]{1,6}", code):
                code = code.zfill(6)
            if selected_code and code != selected_code:
                continue
            try:
                label = "" if fields["action"] is None else str(fields["action"]).strip()
                kind = mapping["actions"].get(label)
                if kind == "IGNORE":
                    ignored += 1
                    continue
                if kind is None:
                    raise ValueError(f"未支持的摘要 {label!r}，请检查 actions 映射")
                if "account" in fields:
                    account = str(fields["account"] or "").strip()
                    if not account:
                        raise ValueError("缺少股东账号")
                    if accounts.setdefault(code, account) != account:
                        raise ValueError(f"{code} 涉及多个股东账号，请按股东账号拆分后核对")
                action = parse_action(fields, [values[i] for i in fee_columns], row, broker, code, kind, mapping)
            except (ValueError, DecimalException, OverflowError) as exc:
                raise ValueError(f"Excel 第 {row} 行：{exc}") from exc
            actions[code].append(action)
        if not actions:
            raise ValueError("文件中没有所选范围的成交或股息记录")
        return actions, ignored
    finally:
        book.close()
