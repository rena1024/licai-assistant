import { describe, expect, it } from 'vitest'
import { calculateConfirmation, deductRedemptionFee, displayedActionUnitPrice, editableActionUnitPrice } from '../components/portfolio/confirmation'

describe('基金流水确认', () => {
  it('赎回填成交净值1.2，列表显示净到账1194及扣费后净值1.194', () => {
    const confirmed = calculateConfirmation({ isAdd: false, shares: 1000, unitPrice: 1.2, fee: 6 })
    const action = { action_type: 'REDEEM', amount: confirmed.amount, shares: 1000, unit_price: 1.2, fee: 6 }
    expect(confirmed.amount).toBe(1194)
    expect(displayedActionUnitPrice(action)).toBe(1.194)
    expect(editableActionUnitPrice(action)).toBe(1.2)
  })

  it('旧流水的扣费后单价不作为编辑时的成交净值继续扣费', () => {
    const action = { action_type: 'REDEEM', amount: 2048.75, shares: 2065.69, unit_price: 0.9918, fee: 2.05 }
    expect(displayedActionUnitPrice(action).toFixed(4)).toBe('0.9918')
    expect(editableActionUnitPrice(action)).toBeCloseTo(2050.8 / 2065.69, 10)
  })

  it('买入列表继续显示成交净值', () => {
    const action = { action_type: 'BUY', amount: 1206, shares: 1000, unit_price: 1.2, fee: 6 }
    expect(displayedActionUnitPrice(action)).toBe(1.2)
    expect(editableActionUnitPrice(action)).toBe(1.2)
  })
  it('将已保存的扣费前赎回金额明确转换为净到账，不使用已被扣费影响的旧净值', () => {
    expect(deductRedemptionFee('2050.80', '2.05')).toBe(2048.75)
  })

  it.each([-1, 0, 2051, Infinity])('拒绝无法从2050.80转换的手续费 %s', fee => {
    expect(() => deductRedemptionFee('2050.80', fee)).toThrow()
  })
  it.each(['', '0', 0])('赎回手续费 %s 按零计算', fee => {
    const result = calculateConfirmation({ isAdd: false, shares: 1000, unitPrice: 1.2, fee })
    expect(result.amount).toBe(1200)
    expect(result.fee).toBe(0)
  })

  it('赎回先填份额，确认净值与手续费后计算净到账', () => {
    const result = calculateConfirmation({ isAdd: false, shares: 1000, unitPrice: 1.2, fee: 6 })
    expect(result.amount).toBe(1194)
    expect(result.unitPrice).toBe(1.2)
    expect(result.fee).toBe(6)
  })

  it('手工到账金额不重复扣费，反推净值时加回手续费', () => {
    const result = calculateConfirmation({ isAdd: false, amount: 1194, shares: 1000, fee: 6 })
    expect(result.amount).toBe(1194)
    expect(result.unitPrice).toBe(1.2)
  })

  it('买入总支出先扣手续费再算份额', () => {
    const result = calculateConfirmation({ isAdd: true, amount: 1206, unitPrice: 1.2, fee: 6 })
    expect(result.shares).toBe(1000)
    expect(result.amount).toBe(1206)
  })

  it('按份额买入时手续费计入总支出', () => {
    const result = calculateConfirmation({ isAdd: true, shares: 1000, unitPrice: 1.2, fee: 6 })
    expect(result.amount).toBe(1206)
  })

  it('按份额买入手续费留空时，按净申购金额计算默认申购费', () => {
    const result = calculateConfirmation({ isAdd: true, shares: 1000, unitPrice: 1.2, feeRate: 0.01 })
    expect(result.fee).toBe(12)
    expect(result.amount).toBe(1212)
  })

  it('买入显式填写零手续费时不使用自动费率', () => {
    const result = calculateConfirmation({ isAdd: true, amount: 1000, shares: 1000, fee: '0', feeRate: 0.015 })
    expect(result.fee).toBe(0)
  })

  it('赎回不套用基金申购费率', () => {
    const result = calculateConfirmation({ isAdd: false, shares: 1000, unitPrice: 1.2, feeRate: 0.015 })
    expect(result.amount).toBe(1200)
    expect(result.fee).toBe(0)
  })

  it.each([-1, 'abc', Infinity])('拒绝非法手续费 %s', fee => {
    expect(calculateConfirmation({ isAdd: false, shares: 1000, unitPrice: 1.2, fee }).error).toBeTruthy()
  })
})
