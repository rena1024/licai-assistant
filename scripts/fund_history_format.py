"""读取支付宝基金交易明细，保留来源订单并转换为基金账本流水。"""
from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import json
import re

from openpyxl import load_workbook


NOTE_PREFIX = 'fund-history:v1|'
BUY_TYPES = {'定投买入', '用户买入', '用户跨TA转换', '用户转换'}
REQUIRED_COLUMNS = ('订单号', '交易时间', '交易类型', '基金名称', '基金代码',
                    '确认金额', '确认份额', '手续费', '确认日期')
ACTION_FIELDS = ('action_type', 'amount', 'shares', 'unit_price', 'fee',
                 'trade_date', 'trade_time', 'status')


def compact(value):
    return re.sub(r'\s+', '', str(value or ''))


def number(value, label, *, money=False):
    try:
        if isinstance(value, bool) or value is None:
            raise ValueError()
        result = Decimal(str(value).strip().replace(',', ''))
        if not result.is_finite() or result < 0:
            raise ValueError()
        if money and result != result.quantize(Decimal('0.01')):
            raise ValueError()
        if not money and result != result.quantize(Decimal('0.000001')):
            raise ValueError()
        return result
    except (InvalidOperation, ValueError):
        raise ValueError(f'{label}须为非负有限数字，金额精确到分、份额最多六位小数') from None


def fund_code(value):
    if isinstance(value, bool):
        raise ValueError('基金代码须为六位数字')
    raw = str(value).strip()
    if isinstance(value, float) and value.is_integer():
        raw = str(int(value))
    if not re.fullmatch(r'[0-9]{1,6}', raw):
        raise ValueError('基金代码须为六位数字')
    return raw.zfill(6)


def parse_datetime(value):
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    raw = str(value).strip().replace('\r', '').replace('\n', '')
    for fmt in ('%Y/%m/%d %H:%M', '%Y-%m-%d %H:%M', '%Y/%m/%d %H:%M:%S',
                '%Y-%m-%d %H:%M:%S', '%Y/%m/%d', '%Y-%m-%d'):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    raise ValueError('日期格式无效，请使用 YYYY/MM/DD HH:MM 或真实 Excel 日期')


def order_id(raw_order):
    if not isinstance(raw_order, str) and (isinstance(raw_order, bool)
                                          or not isinstance(raw_order, int)
                                          or len(str(raw_order)) > 15):
        raise ValueError('订单号须保存为文本，长数字可能已被 Excel 截断精度')
    order = compact(raw_order)
    if not order or len(order) > 200:
        raise ValueError('订单号不能为空或超过 200 个字符')
    return order


def parse_row(fields, row):
    order = order_id(fields['订单号'])
    code = fund_code(fields['基金代码'])
    kind = compact(fields['交易类型'])
    if kind not in BUY_TYPES | {'营销买入', '用户卖出', '机构分红'}:
        raise ValueError(f'不支持的交易类型：{kind}')
    name = compact(fields['基金名称'])
    if not name:
        raise ValueError('基金名称不能为空')
    trade = parse_datetime(fields['交易时间'])
    confirmation = parse_datetime(fields['确认日期'])
    if confirmation.date() < trade.date():
        raise ValueError('确认日期早于交易日期')
    amount = number(fields['确认金额'], '确认金额', money=True)
    shares = number(fields['确认份额'], '确认份额')
    fee = number(fields['手续费'], '手续费', money=True)
    if kind == '机构分红':
        if shares or fee or amount <= 0:
            raise ValueError('现金分红须有正数确认金额，确认份额及手续费须为零')
        action_type, units, unit = 'DIVIDEND', None, None
    else:
        if shares <= 0:
            raise ValueError('买入或卖出的确认份额须大于零')
        units = float(shares)
        if kind == '营销买入':
            if fee:
                raise ValueError('平台赠送记录的手续费须为零')
            action_type, unit = 'BUY', 0.0
        elif kind == '用户卖出':
            if amount <= 0:
                raise ValueError('卖出确认金额须为正数到账金额')
            action_type, unit = 'REDEEM', float((amount + fee) / shares)
        else:
            if amount <= fee:
                raise ValueError('买入确认金额须大于手续费')
            action_type, unit = 'BUY', float((amount - fee) / shares)
    source = dict(order_id=order, business=kind, confirmation_date=confirmation.date().isoformat(),
                  confirmation_time=confirmation.strftime('%H:%M:%S'), source_amount=f'{amount:.2f}',
                  row=row, combination=str(fields.get('组合基金名称') or '').strip())
    return dict(code=code, name=name, order_id=order, action_type=action_type,
                amount=0.0 if kind == '营销买入' else float(amount), shares=units,
                unit_price=unit, fee=float(fee), trade_date=trade.date().isoformat(),
                trade_time=trade.strftime('%H:%M'), status='confirmed',
                note=NOTE_PREFIX + json.dumps(source, ensure_ascii=False, sort_keys=True))


def signature(action):
    """忽略行号、文件排序和展示名称，仅比较入账内容与来源业务。"""
    source = json.loads(action['note'][len(NOTE_PREFIX):])
    return tuple(action.get(k) for k in ACTION_FIELDS) + tuple(
        source.get(k) for k in ('business', 'confirmation_date', 'confirmation_time', 'source_amount'))


def read_actions(path, sheet='position', selected_code=None, excluded_orders=()):
    excluded = {order_id(order) for order in excluded_orders}
    found_exclusions = set()
    book = load_workbook(path, read_only=True, data_only=False)
    try:
        if sheet not in book.sheetnames:
            raise ValueError(f'找不到工作表 {sheet!r}')
        rows = book[sheet].iter_rows(values_only=True)
        header = [str(v or '').strip() for v in next(rows, ())]
        counts = Counter(header)
        if any(counts[name] != 1 for name in REQUIRED_COLUMNS):
            raise ValueError('必填表头缺失或重复：' + '、'.join(n for n in REQUIRED_COLUMNS if counts[n] != 1))
        indices = {name: i for i, name in enumerate(header) if name}
        batches, seen = defaultdict(list), {}
        for row, values in enumerate(rows, 2):
            if not any(v not in (None, '') for v in values):
                continue
            try:
                fields = {name: values[i] if i < len(values) else None for name, i in indices.items()}
                if selected_code and fund_code(fields['基金代码']) != selected_code:
                    continue
                order = order_id(fields['订单号'])
                if order in excluded:
                    found_exclusions.add(order)
                    continue
                action = parse_row(fields, row)
                key = action['code'], action['order_id']
                if key in seen:
                    if signature(seen[key]) != signature(action):
                        raise ValueError(f'订单 {action["order_id"]} 内容冲突')
                    continue
                seen[key] = action
                batches[action['code']].append(action)
            except ValueError as exc:
                raise ValueError(f'工作表 {sheet} 第 {row} 行：{exc}') from exc
        if excluded - found_exclusions:
            raise ValueError('排除订单在指定工作表或基金范围内不存在：' + '、'.join(sorted(excluded-found_exclusions)))
        if not batches:
            raise ValueError('没有可导入的基金交易记录')
        # 源表通常倒序展示。先按交易时刻稳定排序，同一时刻保持原表顺序。
        for actions in batches.values():
            actions.sort(key=lambda a: (a['trade_date'], a['trade_time']))
        return dict(batches)
    finally:
        book.close()
