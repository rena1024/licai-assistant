"""外部资产记账 — 给基金/加密/理财/现金算实现盈亏 + 当前持有成本.

设计:
- FUND: 移动加权平均结转成本，份额批次仍按先入先出消耗以保留持有日期
- CRYPTO: 按 shares 先进先出配对 (BUY/ADD 加 lot, REDEEM 消耗 lot)
- WEALTH/CASH: 按 principal CNY 配对 (DEPOSIT 加 lot, WITHDRAW 消耗 lot, INTEREST/DIVIDEND 直接进 realized)
- BOT: 不走 ledger (OKX 端管理)

每次 REDEEM/WITHDRAW 触发 realized_pnl += proceeds - matched_lot_cost.
"""
from __future__ import annotations
from datetime import date, datetime
from typing import Iterable


SHARE_BASED = ("FUND", "CRYPTO")
PRINCIPAL_BASED = ("WEALTH", "CASH")

ACQUIRE_TYPES = {"BUY", "ADD", "DEPOSIT"}
RELEASE_TYPES = {"REDEEM", "WITHDRAW"}
INCOME_TYPES = {"INTEREST", "DIVIDEND"}  # 直接进 realized, 不消耗 lot


def _parse_date(s: str | None) -> date:
    if not s:
        return date.today()
    s = str(s)[:10]
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return date.today()


def compute_external_state(
    actions: Iterable[dict],
    asset_type: str,
) -> dict:
    """从流水算 cost_amount / shares / realized_pnl / 剩余 lots.

    返回:
      cost_amount: 当前持仓的累计成本 (CNY, 综合本金法)
      shares: 当前份额 (仅 FUND/CRYPTO 有意义)
      realized_pnl: 截至今天的累计已实现盈亏 (含 INTEREST/DIVIDEND)
      holding_dividend: 归属于当前基金剩余份额的现金分红，已包含在 realized_pnl 中
      lots: 剩余 lots
      total_acquired_cost: 历史总投入成本 (含已赎回部分, 用于审计)
      total_release_proceeds: 历史总赎回 (审计)
    """
    if asset_type == "BOT":
        # BOT 不走 ledger
        return {
            "cost_amount": 0.0,
            "cycle_realized": 0.0,
            "diluted_cost": 0.0,
            "shares": 0.0,
            "realized_pnl": 0.0,
            "lots": [],
            "total_acquired_cost": 0.0,
            "total_release_proceeds": 0.0,
            "income_realized": 0.0,
            "holding_dividend": 0.0,
        }

    # 只算 confirmed 状态的 action; pending (OTC 基金 T+1 待确认) 不进 ledger
    confirmed = [a for a in actions if (a.get("status") or "confirmed") == "confirmed"]
    sorted_actions = sorted(confirmed, key=lambda a: (
        _parse_date(a.get("trade_date") or a.get("created_at")),
        a.get("trade_time") or "00:00",
        a.get("id") or 0,
    ))

    is_share_based = asset_type in SHARE_BASED

    # lots: each = {amount, shares, unit_price, date}
    lots: list[dict] = []
    realized_pnl = 0.0
    income_realized = 0.0
    holding_dividend = 0.0
    total_acquired_cost = 0.0
    total_release_proceeds = 0.0
    # 当前持仓周期内的已实现(券商摊薄成本口径): 减仓盈亏摊进剩余成本;
    # 隔夜空仓视为清仓重置, 日内卖光再买回不重置(与招商实测行为一致)
    cycle_realized = 0.0
    flat_date = None

    for a in sorted_actions:
        t = (a.get("action_type") or "").upper()
        amount = float(a.get("amount") or 0)
        shares = float(a.get("shares")) if a.get("shares") is not None else None
        unit_price = float(a.get("unit_price")) if a.get("unit_price") is not None else None
        ad = _parse_date(a.get("trade_date") or a.get("created_at"))

        if t in ACQUIRE_TYPES:
            if is_share_based and flat_date is not None:
                if ad > flat_date:
                    cycle_realized = 0.0        # 隔夜空仓 → 新周期, 老盈亏不再摊
                flat_date = None
            if is_share_based:
                # 必须知道 shares (没填 unit_price 时由 amount/shares 推)
                lot_shares = shares
                if lot_shares is None and unit_price and unit_price > 0 and amount > 0:
                    lot_shares = amount / unit_price
                if lot_shares is None or lot_shares <= 0:
                    # 没法定 lot, 跳过 (用户后面应该会补 shares)
                    continue
                # 含申购费的总支出必须完整分摊到份额成本；流水中的 unit_price 仍保留净值。
                if asset_type == "FUND" or float(a.get("fee") or 0) > 0:
                    lot_unit = amount / lot_shares
                else:
                    lot_unit = unit_price if unit_price else (amount / lot_shares if lot_shares > 0 else 0)
                lots.append({"amount": amount, "shares": lot_shares, "unit_price": lot_unit, "date": ad})
            else:
                # principal-based
                if amount <= 0:
                    continue
                lots.append({"amount": amount, "shares": None, "unit_price": None, "date": ad})
            total_acquired_cost += amount

        elif t in RELEASE_TYPES:
            proceeds = amount if amount > 0 else 0
            if is_share_based:
                consume_shares = shares if shares is not None else (
                    proceeds / unit_price if unit_price and unit_price > 0 else None
                )
                if consume_shares is None or consume_shares <= 0:
                    continue
                if asset_type == "FUND" and lots:
                    # 金额是实际总支出，不能用四舍五入后的净值反推成本。
                    # 赎回前将剩余成本按份额均摊，各批日期及份额不变；随后只按
                    # FIFO 消耗批次份额，结转成本自然等于赎回份额 × 当前平均成本。
                    held_shares = sum(lot["shares"] for lot in lots)
                    # 历史分红按赎回前的份额比例移出持有收益，仍保留在已实现总账。
                    holding_dividend *= max(0.0, 1 - consume_shares / held_shares)
                    average_cost = sum(lot["amount"] for lot in lots) / held_shares
                    for lot in lots:
                        lot["unit_price"] = average_cost
                        lot["amount"] = lot["shares"] * average_cost
                # 按先入先出消耗份额批次；基金成本已单独按平均成本分摊。
                remaining = consume_shares
                consumed_cost = 0.0
                while remaining > 1e-9 and lots:
                    lot = lots[0]
                    if lot["shares"] <= remaining + 1e-9:
                        consumed_cost += lot["shares"] * (lot["unit_price"] or 0)
                        remaining -= lot["shares"]
                        lots.pop(0)
                    else:
                        consumed_cost += remaining * (lot["unit_price"] or 0)
                        lot["shares"] -= remaining
                        lot["amount"] = lot["shares"] * (lot["unit_price"] or 0)
                        remaining = 0
                # proceeds 推断 (没填 amount 时用 shares × unit_price)
                if proceeds == 0 and unit_price and unit_price > 0:
                    proceeds = consume_shares * unit_price
                realized_pnl += proceeds - consumed_cost
                cycle_realized += proceeds - consumed_cost
                total_release_proceeds += proceeds
                if not lots:
                    flat_date = ad              # 卖光了: 若隔夜未买回, 下次买入重置周期
            else:
                # principal-based: 按 CNY 配对
                if proceeds <= 0:
                    continue
                # WEALTH/CASH: 用户填了 interest_part → 这部分进 realized_pnl,
                # 只用 (proceeds - interest_part) 去消耗本金 lot. 没填则全当本金 (FIFO 兜底).
                interest_part_raw = a.get("interest_part")
                interest_part = float(interest_part_raw) if interest_part_raw is not None else 0.0
                principal_consume = max(0.0, proceeds - interest_part)
                remaining = principal_consume
                consumed_cost = 0.0
                while remaining > 1e-9 and lots:
                    lot = lots[0]
                    if lot["amount"] <= remaining + 1e-9:
                        consumed_cost += lot["amount"]
                        remaining -= lot["amount"]
                        lots.pop(0)
                    else:
                        consumed_cost += remaining
                        lot["amount"] -= remaining
                        remaining = 0
                # realized = (本金消耗回款 - 配对成本) + 利息部分
                # FIFO 配对正常时 principal_consume == consumed_cost, 差额来自利息或超额赎回
                realized_pnl += (principal_consume - consumed_cost) + interest_part
                total_release_proceeds += proceeds

        elif t in INCOME_TYPES:
            # INTEREST / DIVIDEND 直接进 realized, 不动 lot
            realized_pnl += amount
            income_realized += amount
            if asset_type == "FUND" and t == "DIVIDEND" and lots:
                holding_dividend += amount
            if is_share_based:
                cycle_realized += amount        # 现金分红降低摊薄成本

        elif t == "SPLIT" and is_share_based:
            # 份额拆分/折算 (1份→F份): factor 存在 shares 字段。当时全部 lot 份额×F、
            # 单价÷F, 成本一分不动 —— 拆分不是交易, 不产生任何盈亏; 逐 lot 缩放保证
            # 之后赎回使用拆分后的份额和成本，拆分不改变总成本。
            f = float(shares) if shares else 0.0
            if f > 0 and abs(f - 1) > 1e-9:
                for lot in lots:
                    if lot.get("shares"):
                        lot["shares"] *= f
                        if lot.get("unit_price"):
                            lot["unit_price"] /= f

    cost_amount = sum(l["amount"] for l in lots)
    shares_total = sum(l["shares"] for l in lots if l.get("shares") is not None) if is_share_based else 0.0
    if not lots:
        cycle_realized = 0.0                    # 当前没持仓就没有"周期内"概念
        holding_dividend = 0.0

    return {
        "cost_amount": round(cost_amount, 4),
        "cycle_realized": round(cycle_realized, 2),
        # 摊薄成本(券商口径): 剩余lot成本 − 周期内已实现(亏损为负 → 成本抬高)
        "diluted_cost": round(cost_amount - cycle_realized, 4),
        "shares": round(shares_total, 6) if is_share_based else 0.0,
        "realized_pnl": round(realized_pnl, 2),
        "income_realized": round(income_realized, 2),
        "holding_dividend": round(holding_dividend, 2),
        "lots": [
            {
                "amount": round(l["amount"], 4),
                "shares": round(l["shares"], 6) if l.get("shares") is not None else None,
                "unit_price": round(l["unit_price"], 6) if l.get("unit_price") is not None else None,
                "date": l["date"].isoformat(),
            } for l in lots
        ],
        "total_acquired_cost": round(total_acquired_cost, 2),
        "total_release_proceeds": round(total_release_proceeds, 2),
    }
