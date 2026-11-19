function isRedemption(action) {
  return action.action_type === 'REDEEM' || action.action_type === 'SELL'
}

// 列表中的赎回单价按净到账计算；不再对已扣费的金额减一次手续费。
export function displayedActionUnitPrice(action) {
  if (isRedemption(action)) {
    const amount = Number(action.amount), shares = Number(action.shares)
    return amount > 0 && shares > 0 ? amount / shares : null
  }
  return action.unit_price == null ? null : Number(action.unit_price)
}

// 编辑时需要成交净值，旧流水可能存的是扣费后单价，因此从净到账与费用还原。
export function editableActionUnitPrice(action) {
  if (isRedemption(action)) {
    const amount = Number(action.amount), shares = Number(action.shares), fee = Number(action.fee || 0)
    if (amount > 0 && shares > 0) return (amount + fee) / shares
  }
  return action.unit_price == null ? null : Number(action.unit_price)
}

export function deductRedemptionFee(grossAmount, fee) {
  const gross = Number(grossAmount), charge = Number(fee)
  if (!Number.isFinite(gross) || !Number.isFinite(charge) || charge <= 0 || gross <= charge) {
    throw new Error('原金额须大于手续费，手续费须为正数且为有限数字')
  }
  return Number((gross - charge).toFixed(2))
}

// 买入金额是含费总支出，赎回金额是扣费后到账；只有反推净值时才还原成交金额。
export function calculateConfirmation({ isAdd, amount = '', shares = '', unitPrice = '', fee = '', feeRate = 0 }) {
  const number = value => value === '' || value == null ? 0 : Number(value)
  const a = number(amount), s = number(shares), u = number(unitPrice)
  const rate = isAdd ? number(feeRate) : 0
  let autoFee = 0
  if (rate > 0) {
    if (a > 0) autoFee = Number((a - a / (1 + rate)).toFixed(2))
    else if (s > 0 && u > 0) autoFee = Number((s * u * rate).toFixed(2))
  }
  const effectiveFee = fee === '' || fee == null ? autoFee : number(fee)
  if ([a, s, u, effectiveFee].some(value => !Number.isFinite(value) || value < 0)) {
    return { error: '金额、份额、净值和手续费须为非负有限数字' }
  }
  const finalAmount = a > 0 ? a : (s > 0 && u > 0
    ? Number((s * u + (isAdd ? effectiveFee : -effectiveFee)).toFixed(2)) : 0)
  const tradeAmount = finalAmount > 0 ? (isAdd ? finalAmount - effectiveFee : finalAmount + effectiveFee) : 0
  if ((finalAmount > 0 && tradeAmount <= 0) || (s > 0 && u > 0 && finalAmount <= 0)) {
    return { error: isAdd ? '手续费须小于买入总金额' : '扣除手续费后的到账金额须大于零' }
  }
  return {
    amount: finalAmount,
    fee: effectiveFee,
    autoFee,
    tradeAmount,
    shares: s || (u > 0 && tradeAmount > 0 ? tradeAmount / u : 0),
    unitPrice: u || (s > 0 && tradeAmount > 0 ? tradeAmount / s : 0),
  }
}
