import React from 'react'
import { fmtMoney } from '../../helpers'
import { fetchJSON } from '../../hooks/useApi'
import { calculateConfirmation, deductRedemptionFee, displayedActionUnitPrice, editableActionUnitPrice } from './confirmation'
import CashDividendForm from './forms/CashDividendForm'

const ACTION_GRID_COLUMNS = 'grid-cols-[80px_60px_repeat(3,minmax(0,1fr))_160px]'

// AssetActionsModal — 流水查看 + pending 确认.
export function AssetActionsModal({ asset, onClose, onChanged }) {
  const [actions, setActions] = React.useState([])
  const [state, setState] = React.useState(null)
  const [loading, setLoading] = React.useState(true)
  const [confirmTarget, setConfirmTarget] = React.useState(null)
  const [editTarget, setEditTarget] = React.useState(null)
  const [addingDividend, setAddingDividend] = React.useState(false)
  const [dividendMessage, setDividendMessage] = React.useState('')
  const [loadError, setLoadError] = React.useState('')

  const reload = React.useCallback(async () => {
    setLoading(true)
    setLoadError('')
    try {
      const d = await fetchJSON(`/api/assets/${asset.id}/actions`)
      setActions(d.actions || [])
      setState(d.state || null)
    } catch (e) { setLoadError(`流水加载失败：${e.message}`) }
    finally { setLoading(false) }
  }, [asset.id])

  React.useEffect(() => { reload() }, [reload])

  React.useEffect(() => {
    const onKey = e => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const deleteAction = async (id) => {
    if (!confirm('确定删除这条流水？后续 cost/shares 会按剩余流水重新计算')) return
    await fetchJSON(`/api/assets/${asset.id}/actions/${id}`, { method: 'DELETE' })
    onChanged?.()
    reload()
  }

  const editPendingAmount = async (a) => {
    const cur = parseFloat(a.amount) || 0
    const next = prompt(`改这条 pending 流水的金额 (CNY)\n常用于基金限额变化, 状态仍保持 pending`, cur.toFixed(2))
    if (next == null) return
    const v = parseFloat(next)
    if (!v || v <= 0) { alert('金额必须为正数'); return }
    if (Math.abs(v - cur) < 0.005) return
    try {
      await fetchJSON(`/api/assets/${asset.id}/actions/${a.id}`, {
        method: 'PATCH', body: JSON.stringify({ amount: v }),
      })
      onChanged?.()
      reload()
    } catch (e) { alert(e.message) }
  }

  const colorByType = {
    BUY: 'text-bull-bright', ADD: 'text-bull-bright', DEPOSIT: 'text-bull-bright',
    REDEEM: 'text-bear-bright', WITHDRAW: 'text-bear-bright',
    INTEREST: 'text-info', DIVIDEND: 'text-info', SPLIT: 'text-accent',
  }
  const labelByType = {
    BUY: '买入', ADD: '加仓', REDEEM: '赎回', SPLIT: '份额拆分',
    DEPOSIT: '存入', WITHDRAW: '取出',
    INTEREST: '利息', DIVIDEND: '分红',
  }

  return (
    <div className="fixed inset-0 z-[200] flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <div className="bg-surface-2 border border-border rounded-xl p-5 w-[640px] max-w-[95vw] max-h-[85vh] overflow-y-auto"
        onClick={e => e.stopPropagation()}>
        <div className="flex items-baseline justify-between gap-2 mb-3">
          <h3 title={asset.name} className="text-[14px] font-semibold text-text-bright m-0 min-w-0 truncate">流水 · {asset.name}</h3>
          <div className="flex items-center gap-2 shrink-0">
            {asset.asset_type === 'FUND' && !addingDividend && (
              <button onClick={() => { setAddingDividend(true); setDividendMessage('') }}
                className="px-2 py-1 rounded text-[11px] border border-accent/60 text-accent hover:bg-accent/10 cursor-pointer">
                补录现金分红
              </button>
            )}
            <button onClick={onClose} aria-label="关闭流水" className="text-text-dim hover:text-text text-[18px] leading-none px-2 cursor-pointer">×</button>
          </div>
        </div>

        {addingDividend && (
          <CashDividendForm assetId={asset.id}
            onCancel={() => setAddingDividend(false)}
            onSaved={keepOpen => {
              setAddingDividend(keepOpen)
              setDividendMessage('现金分红已录入')
              reload()
              onChanged?.()
            }} />
        )}
        {dividendMessage && <div role="status" className="text-info text-[11.5px] mb-3">{dividendMessage}</div>}
        {loadError && <div role="alert" className="text-bear-bright text-[11.5px] mb-3">{loadError}</div>}

        {state && (
          <div className="grid grid-cols-3 gap-2 mb-3 text-[11.5px]">
            <div className="bg-surface-3 rounded-md px-2 py-1.5">
              <div className="text-text-dim text-[10px] mb-0.5">当前成本</div>
              <div className="font-mono text-text">¥{fmtMoney(state.cost_amount)}</div>
            </div>
            {(asset.asset_type === 'FUND' || asset.asset_type === 'CRYPTO') && (
              <div className="bg-surface-3 rounded-md px-2 py-1.5">
                <div className="text-text-dim text-[10px] mb-0.5">持有份额</div>
                <div className="font-mono text-text">{state.shares?.toFixed(4)}</div>
              </div>
            )}
            <div className="bg-surface-3 rounded-md px-2 py-1.5">
              <div className="text-text-dim text-[10px] mb-0.5">累计已实现</div>
              <div className={`font-mono ${state.realized_pnl >= 0 ? 'text-bull-bright' : 'text-bear-bright'}`}>
                {state.realized_pnl >= 0 ? '+' : ''}¥{fmtMoney(Math.abs(state.realized_pnl))}
              </div>
            </div>
          </div>
        )}

        {loading ? (
          <div className="text-center text-text-dim text-[12px] py-4">加载中...</div>
        ) : actions.length === 0 ? (
          <div className="text-center text-text-dim text-[12px] py-4">暂无流水</div>
        ) : (
          <div className="border border-border-subtle rounded-md overflow-hidden">
            <div className={`grid ${ACTION_GRID_COLUMNS} gap-2 px-2 py-1.5 text-[10px] text-text-dim bg-surface-3 border-b border-border-subtle font-medium tracking-wider`}>
              <div>日期</div>
              <div>类型</div>
              <div className="text-right">金额</div>
              <div className="text-right">份额</div>
              <div className="text-right">单价</div>
              <div className="text-right">操作</div>
            </div>
            {[...actions]
              .sort((x, y) => {
                // 展示倒序: 新流水在前. 后端按 trade_date ASC + id ASC 给 ledger 算账, 这里翻过来
                const dx = x.trade_date || (x.created_at || '').slice(0, 10) || ''
                const dy = y.trade_date || (y.created_at || '').slice(0, 10) || ''
                if (dy !== dx) return dy.localeCompare(dx)
                return (y.id || 0) - (x.id || 0)
              })
              .map(a => {
              const isPending = (a.status || 'confirmed') === 'pending'
              const displayUnitPrice = displayedActionUnitPrice(a)
              return (
                <div key={a.id} className={`grid ${ACTION_GRID_COLUMNS} gap-2 px-2 py-2 text-[11.5px] items-center border-b border-border-subtle last:border-b-0 ${isPending ? 'bg-warn/5' : ''}`}>
                  <div className="font-mono text-[10.5px] text-text-dim whitespace-nowrap">
                    {(a.trade_date || a.created_at || '').slice(0, 10) || '--'}
                  </div>
                  <div className={`font-medium ${colorByType[a.action_type] || 'text-text'}`}>
                    {labelByType[a.action_type] || a.action_type}
                  </div>
                  <div className="text-right font-mono">
                    {a.amount > 0 ? `¥${fmtMoney(a.amount)}` : <span className="text-text-muted">--</span>}
                  </div>
                  <div className="text-right font-mono text-[11px]">
                    {a.shares != null ? parseFloat(a.shares).toFixed(4) : '--'}
                  </div>
                  <div className="text-right font-mono text-[11px]"
                    title={a.action_type === 'REDEEM' || a.action_type === 'SELL' ? '扣费后净值 = 实际到账金额 ÷ 赎回份额' : '成交净值 / 单价'}>
                    {displayUnitPrice != null ? displayUnitPrice.toFixed(4) : '--'}
                  </div>
                  <div className="flex gap-1 items-center justify-end">
                    {isPending ? (
                      <>
                        <button onClick={() => editPendingAmount(a)}
                          title="改金额 (限额变化等), 状态保持 pending"
                          className="px-1.5 py-[2px] rounded text-[10px] border border-accent/60 text-accent hover:bg-accent/10 cursor-pointer">
                          改额
                        </button>
                        <button onClick={() => setConfirmTarget(a)}
                          className="px-1.5 py-[2px] rounded text-[10px] border border-warn text-warn hover:bg-warn/10 cursor-pointer">
                          确认
                        </button>
                      </>
                    ) : (
                      <button onClick={() => setEditTarget(a)}
                        title="改金额 / 份额 / 单价 / 手续费"
                        className="px-1.5 py-[2px] rounded text-[10px] border border-accent/60 text-accent hover:bg-accent/10 cursor-pointer">
                        编辑
                      </button>
                    )}
                    {a.note !== 'initial (auto-migrated)' && (
                      <button onClick={() => deleteAction(a.id)}
                        className="px-1.5 py-[2px] rounded text-[10px] border border-bear/40 text-bear hover:bg-bear/10 cursor-pointer">
                        删
                      </button>
                    )}
                    {isPending && (
                      <span className="text-[9.5px] px-1 py-[1px] rounded bg-warn/15 text-warn border border-warn/40">⏳</span>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        )}
        {actions.some(a => a.action_type === 'REDEEM' || a.action_type === 'SELL') && (
          <div className="mt-2 text-[10.5px] text-text-muted">赎回金额为实际到账，赎回单价显示扣费后净值（到账金额 ÷ 份额）。</div>
        )}
      </div>

      {confirmTarget && (
        <ConfirmActionModal asset={asset} action={confirmTarget}
          onClose={() => setConfirmTarget(null)}
          onDone={() => { setConfirmTarget(null); reload(); onChanged?.() }} />
      )}
      {editTarget && (
        <EditActionModal asset={asset} action={editTarget}
          onClose={() => setEditTarget(null)}
          onDone={() => { setEditTarget(null); reload(); onChanged?.() }} />
      )}
    </div>
  )
}

function EditActionModal({ asset, action, onClose, onDone }) {
  // 已 confirmed 流水的"改"弹窗: 改 amount / shares / unit_price / fee
  // 改完后端会同步 asset cost_amount/shares
  // 联动只在用户修改后触发，避免打开旧流水时自动覆盖历史金额。
  const isRedeem = action.action_type === 'REDEEM' || action.action_type === 'SELL'
  const [amount, setAmount] = React.useState(action.amount != null ? String(action.amount) : '')
  const [shares, setShares] = React.useState(action.shares != null ? String(action.shares) : '')
  const [unitPrice, setUnitPrice] = React.useState(() => {
    const value = editableActionUnitPrice(action)
    return value == null ? '' : (isRedeem ? String(Number(value.toFixed(6))) : String(value))
  })
  const [fee, setFee] = React.useState(action.fee != null ? String(action.fee) : '')
  const [tradeDate, setTradeDate] = React.useState((action.trade_date || (action.created_at || '').slice(0, 10) || '').slice(0, 10))
  const [tradeTime, setTradeTime] = React.useState((action.trade_time || '').slice(0, 5))   // 成交时刻 HH:MM
  const [lastEdit, setLastEdit] = React.useState(null)  // 'amount' | 'shares' | 'unitPrice'
  const [deductedSavedFee, setDeductedSavedFee] = React.useState(false)
  const [busy, setBusy] = React.useState(false)
  const [err, setErr] = React.useState('')

  const hasShares = asset.asset_type === 'FUND' || asset.asset_type === 'CRYPTO'

  // 修改份额、净值或手续费时重算金额；手填金额则反推净值，赎回金额始终为净到账。
  React.useEffect(() => {
    if (!hasShares || !lastEdit) return
    const editedAmount = lastEdit === 'amount'
    const result = calculateConfirmation({
      isAdd: !isRedeem, amount: editedAmount ? amount : '', shares,
      unitPrice: editedAmount ? '' : unitPrice, fee,
    })
    if (result.error) return
    if (editedAmount && result.unitPrice > 0) {
      const next = result.unitPrice.toFixed(6)
      if (next !== unitPrice) setUnitPrice(next)
    } else if (!editedAmount && Number(shares) > 0 && Number(unitPrice) > 0 && result.amount > 0) {
      const next = result.amount.toFixed(2)
      if (next !== amount) setAmount(next)
    }
  }, [amount, shares, unitPrice, fee, lastEdit, hasShares, isRedeem])

  React.useEffect(() => {
    const onKey = e => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const submit = async () => {
    setErr('')
    const body = {}
    if ([amount, shares, unitPrice, fee].some(value => value !== '' && (!Number.isFinite(Number(value)) || Number(value) < 0))) {
      setErr('金额、份额、净值和手续费须为非负有限数字'); return
    }
    if (hasShares && lastEdit && lastEdit !== 'amount') {
      const result = calculateConfirmation({ isAdd: !isRedeem, shares, unitPrice, fee })
      if (result.error) { setErr(result.error); return }
    }
    const a = parseFloat(amount), s = parseFloat(shares), u = parseFloat(unitPrice), f = fee === '' ? 0 : parseFloat(fee)
    // 只把"用户改过的"字段传给后端 — 简单判定: 跟 action 原值不同就算改了
    if (!isNaN(a) && a !== (action.amount ?? 0)) body.amount = a
    if (hasShares && !isNaN(s) && s !== (action.shares ?? 0)) body.shares = s
    if (!isNaN(u) && u !== (action.unit_price ?? 0)) body.unit_price = u
    if (!isNaN(f) && f !== (action.fee ?? 0)) body.fee = f
    const origDate = (action.trade_date || (action.created_at || '').slice(0, 10) || '').slice(0, 10)
    if (tradeDate && tradeDate !== origDate) body.trade_date = tradeDate
    const origTime = (action.trade_time || '').slice(0, 5)
    if (tradeTime !== origTime) body.trade_time = tradeTime   // "" → 清空回退录入时间
    if (Object.keys(body).length === 0) { onClose(); return }
    setBusy(true)
    try {
      await fetchJSON(`/api/assets/${asset.id}/actions/${action.id}`, {
        method: 'PATCH', body: JSON.stringify(body),
      })
      onDone?.()
    } catch (e) { setErr(e.message) }
    finally { setBusy(false) }
  }

  return (
    <div className="fixed inset-0 z-[210] flex items-center justify-center bg-black/70 backdrop-blur-sm" onClick={onClose}>
      <div className="bg-surface-2 border border-border rounded-xl p-5 w-[420px] max-w-[95vw] space-y-3"
        onClick={e => e.stopPropagation()}>
        <div className="flex items-baseline justify-between">
          <h3 className="text-[14px] font-semibold text-text-bright m-0">编辑流水</h3>
          <button onClick={onClose} className="text-text-dim hover:text-text text-[18px] leading-none px-2 cursor-pointer">×</button>
        </div>
        <div className="text-[11px] text-text-dim">
          类型 <span className="font-mono text-text">{action.action_type}</span>
          <span className="mx-1.5">·</span>
          保存后按流水重算持有份额、成本和盈亏
        </div>

        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="text-[11.5px] text-text-dim block mb-1">日期</label>
            <input type="date" value={tradeDate} onChange={e => setTradeDate(e.target.value)}
              className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-[13px] font-mono outline-none focus:border-accent" />
          </div>
          <div>
            <label className="text-[11.5px] text-text-dim block mb-1">时刻 <span className="text-text-muted text-[10px]">可空</span></label>
            <input type="time" value={tradeTime} onChange={e => setTradeTime(e.target.value)}
              title="成交时刻(可选), 留空用录入时间, 供分时图打点"
              className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-[13px] font-mono outline-none focus:border-accent text-text-dim" />
          </div>
        </div>

        <div>
          <label className="text-[11.5px] text-text-dim block mb-1">
            {isRedeem ? '实际到账金额 (CNY, 已扣手续费)' : '金额 (CNY, 含手续费)'}
            {hasShares && lastEdit && lastEdit !== 'amount' && (
              <span className="text-text-muted text-[10px] ml-1">{isRedeem
                ? '— 自动 = 份额×净值 − 手续费' : '— 自动 = 份额×单价 + 手续费'}</span>
            )}
          </label>
          <input type="number" inputMode="decimal" value={amount}
            onChange={e => { setAmount(e.target.value); setLastEdit('amount') }}
            className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-[13px] font-mono outline-none focus:border-accent" />
          {isRedeem && Number(fee) > 0 && (
            <div className="mt-1.5 text-[10.5px] text-text-muted">
              <span>仅当这笔旧记录的原金额尚未扣手续费时使用，已是到账金额请勿重复扣除。</span>
              <button type="button" disabled={deductedSavedFee}
                onClick={() => {
                  try {
                    setAmount(deductRedemptionFee(amount, fee).toFixed(2))
                    setLastEdit('amount')
                    setDeductedSavedFee(true)
                    setErr('')
                  } catch (error) { setErr(error.message) }
                }}
                className="mt-1 px-2 py-1 rounded border border-border text-accent cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed">
                {deductedSavedFee ? '本次已扣除手续费，请保存' : '原金额未扣费：扣除一次手续费'}
              </button>
            </div>
          )}
        </div>

        {hasShares && (
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-[11.5px] text-text-dim block mb-1">份额</label>
              <input type="number" inputMode="decimal" value={shares}
                onChange={e => { setShares(e.target.value); setLastEdit('shares') }}
                className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-[13px] font-mono outline-none focus:border-accent" />
            </div>
            <div>
              <label className="text-[11.5px] text-text-dim block mb-1">
                {isRedeem ? '成交净值（扣费前）' : '净值 / 单价'}
                {lastEdit === 'amount' && (
                  <span className="text-text-muted text-[10px] ml-1">{isRedeem
                    ? '— 自动 = (到账金额＋费)/份额' : '— 自动 = (金额−费)/份额'}</span>
                )}
              </label>
              <input type="number" inputMode="decimal" value={unitPrice}
                onChange={e => { setUnitPrice(e.target.value); setLastEdit('unitPrice') }}
                className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-[13px] font-mono outline-none focus:border-accent" />
            </div>
          </div>
        )}

        <div>
          <label className="text-[11.5px] text-text-dim block mb-1">手续费 (CNY)</label>
          <input type="number" inputMode="decimal" value={fee}
            onChange={e => { setFee(e.target.value); setLastEdit('fee') }}
            className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-[13px] font-mono outline-none focus:border-accent" />
        </div>

        {err && <div className="text-[11px] text-bear-bright">{err}</div>}

        <div className="flex gap-2 pt-1">
          <button onClick={submit} disabled={busy}
            className="flex-1 px-4 py-2 rounded-lg bg-accent text-bg font-medium text-[13px] hover:opacity-90 disabled:opacity-50 cursor-pointer">
            {busy ? '...' : '保存'}
          </button>
          <button onClick={onClose}
            className="px-4 py-2 rounded-lg border border-border text-text-dim hover:text-text hover:border-border-med text-[13px] cursor-pointer">
            取消
          </button>
        </div>
      </div>
    </div>
  )
}

function ConfirmActionModal({ asset, action, onClose, onDone }) {
  // 三种 pending 形态:
  //  REDEEM (赎回): shares 已知, 待确认 amount + unit_price
  //  ADD/BUY · amount 模式: amount 已知, 待确认 shares + unit_price
  //  ADD/BUY · shares 模式: shares 已知 (按份额定投), 只需补 unit_price, amount 自算
  const isAdd = action.action_type === 'ADD' || action.action_type === 'BUY'
  const knownShares = action.shares ? parseFloat(action.shares) : 0
  const knownAmount = parseFloat(action.amount) || 0
  const isSharesMode = isAdd && knownShares > 0 && knownAmount === 0

  // shares 模式: 份额 disabled+预填, 金额留空让它从 shares*unit_price 自算
  // amount 模式: 金额预填, 份额留空
  // REDEEM: 份额已知 disabled
  const [amount, setAmount] = React.useState(
    isSharesMode ? '' : (isAdd ? String(knownAmount) : '')
  )
  const [shares, setShares] = React.useState(
    isSharesMode ? String(knownShares) : (isAdd ? '' : String(knownShares))
  )
  const [unitPrice, setUnitPrice] = React.useState('')
  const [fee, setFee] = React.useState('')
  const [busy, setBusy] = React.useState(false)
  const [err, setErr] = React.useState('')

  const a = parseFloat(amount) || 0
  const s = parseFloat(shares) || 0
  const u = parseFloat(unitPrice) || 0
  // 申购费率外扣: 没手填 fee 时, 对场外基金申购按费率自动算 (净额=金额/(1+费率))
  const feeRate = isAdd ? (parseFloat(asset.purchase_fee_rate) || 0) : 0
  const confirmation = calculateConfirmation({ isAdd, amount, shares, unitPrice, fee, feeRate })
  const autoFee = confirmation.autoFee || 0
  const effFee = confirmation.fee || 0
  const netForShares = Math.max(0, confirmation.tradeAmount || 0)

  // 买入用净申购金额，赎回用扣费前成交金额反推净值。
  const inferredUnit = (confirmation.unitPrice > 0 && !u) ? confirmation.unitPrice.toFixed(4) : ''
  const inferredShares = (confirmation.shares > 0 && !s) ? confirmation.shares.toFixed(4) : ''
  const inferredAmount = (confirmation.amount > 0 && !a) ? confirmation.amount.toFixed(2) : ''

  const submit = async () => {
    setErr('')
    if (confirmation.error) { setErr(confirmation.error); return }
    const finalAmount = confirmation.amount
    if (!finalAmount || finalAmount <= 0) { setErr('金额必填'); return }
    if (isAdd && !s && !u) { setErr('申购确认: 至少填份额或净值'); return }
    setBusy(true)
    try {
      const body = { amount: finalAmount }
      if (s > 0) body.shares = s
      if (u > 0) body.unit_price = u
      body.fee = effFee
      await fetchJSON(`/api/assets/${asset.id}/actions/${action.id}/confirm`, {
        method: 'PUT', body: JSON.stringify(body),
      })
      onDone?.()
    } catch (e) { setErr(e.message) }
    finally { setBusy(false) }
  }

  return (
    <div className="fixed inset-0 z-[210] flex items-center justify-center bg-black/70 backdrop-blur-sm" onClick={onClose}>
      <div className="bg-surface-2 border border-border rounded-xl p-5 w-[420px] max-w-[95vw] space-y-3"
        onClick={e => e.stopPropagation()}>
        <div className="flex items-baseline justify-between">
          <h3 className="text-[14px] font-semibold text-text-bright m-0">
            确认 {isAdd ? '申购' : '赎回'}
          </h3>
          <button onClick={onClose} className="text-text-dim hover:text-text text-[18px] leading-none px-2 cursor-pointer">×</button>
        </div>
        <div className="text-[11px] text-text-dim">
          原申请: {isSharesMode
            ? <>份额 <span className="font-mono text-text">{knownShares.toFixed(4)}</span> <span className="text-text-muted">(按份额定投, 只需补单价)</span></>
            : isAdd
              ? <>金额 <span className="font-mono text-text">¥{fmtMoney(knownAmount)}</span></>
              : <>份额 <span className="font-mono text-text">{knownShares.toFixed(4)}</span></>
          } · 日期 <span className="font-mono text-text">{action.trade_date || '--'}</span>
        </div>

        <div>
          <label className="text-[11.5px] text-text-dim block mb-1">
            {isAdd ? '金额 (CNY, 含手续费)' : '实际到账金额 (CNY, 已扣手续费)'}
            {isSharesMode
              ? <span className="text-text-muted text-[10px]"> — 留空将自动 = 份额 × 单价 + 手续费</span>
              : isAdd
                ? <span className="text-text-muted text-[10px]"> — 你实际付出的总额, 含手续费</span>
                : <span className="text-text-muted text-[10px]"> — 留空自动计算：份额 × 净值 − 手续费</span>}
          </label>
          <input type="number" inputMode="decimal" placeholder={inferredAmount || '0'}
            value={amount} onChange={e => setAmount(e.target.value)}
            className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-[13px] font-mono outline-none focus:border-accent" />
        </div>

        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="text-[11.5px] text-text-dim block mb-1">
              {isSharesMode ? '成交份额' : isAdd ? '成交份额 *' : '份额'}
            </label>
            <input type="number" inputMode="decimal"
              placeholder={inferredShares || (isAdd && !isSharesMode ? '必填或填净值' : '已知')}
              disabled={!isAdd || isSharesMode}
              value={shares} onChange={e => setShares(e.target.value)}
              className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-[13px] font-mono outline-none focus:border-accent disabled:opacity-60" />
          </div>
          <div>
            <label className="text-[11.5px] text-text-dim block mb-1">
              {isAdd ? '净值 / 单价' : '成交净值（扣费前）'} {isSharesMode
                ? <span className="text-warn text-[10px]">*</span>
                : <span className="text-text-muted text-[10px]">可空</span>}
            </label>
            <input type="number" inputMode="decimal" placeholder={inferredUnit || (isSharesMode ? '必填' : '可选')}
              value={unitPrice} onChange={e => setUnitPrice(e.target.value)}
              autoFocus={isSharesMode}
              className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-[13px] font-mono outline-none focus:border-accent" />
          </div>
        </div>

        <div>
          <label className="text-[11.5px] text-text-dim block mb-1">
            手续费 (CNY) <span className="text-text-muted text-[10px]">{isAdd
              ? '— 含在上方金额里, 单填方便看净额'
              : '— 留空为 0，填写后从赎回总额扣除'}</span>
            {feeRate > 0 && (
              <span className="text-accent text-[10px] ml-1">
                · 申购费率 {(feeRate * 100).toFixed(2)}% 自动外扣 {autoFee > 0 ? `¥${autoFee.toFixed(2)}` : ''}
              </span>
            )}
          </label>
          <div className="flex gap-2 items-stretch">
            <input type="number" inputMode="decimal"
              placeholder={autoFee > 0 ? `${autoFee.toFixed(2)} (申购费率自动)` : '0 (默认无费)'}
              value={fee} onChange={e => setFee(e.target.value)}
              className="flex-1 bg-bg border border-border rounded-lg px-3 py-2 text-[13px] font-mono outline-none focus:border-accent" />
            <button type="button"
              onClick={() => {
                // 招商证券万1.854 5元起 (跟 stock 端用同一档, 用户已存的费率)
                const calc = Math.max(5, a * 0.0001854)
                setFee(calc.toFixed(2))
              }}
              disabled={!a}
              className="px-2.5 py-1 rounded-lg border border-border text-text-dim hover:text-text hover:border-border-med text-[10.5px] cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
              title="按招商证券 万1.854 / 最低 5 元 算一下">
              券商费率
            </button>
          </div>
          {(effFee > 0 || !isAdd) && confirmation.amount > 0 && (
            <div className="text-[10.5px] text-text-muted mt-1">
              {isAdd
                ? <>净申购金额 ¥{netForShares.toFixed(2)} (= 总支出 ¥{confirmation.amount.toFixed(2)} − 手续费 ¥{effFee.toFixed(2)}</>
                : <>实际到账 ¥{confirmation.amount.toFixed(2)} (= 赎回总额 ¥{netForShares.toFixed(2)} − 手续费 ¥{effFee.toFixed(2)}</>}
              {fee === '' && autoFee > 0 && <span className="text-accent"> 申购费率</span>})
              {confirmation.shares > 0 && (
                <span className="ml-1.5">· {isAdd ? '成交净值' : '扣费后净值'} ≈ <span className="font-mono">{((isAdd ? netForShares : confirmation.amount) / confirmation.shares).toFixed(4)}</span></span>
              )}
            </div>
          )}
        </div>

        {err && <div className="text-[11px] text-bear-bright">{err}</div>}

        <div className="flex gap-2 pt-1">
          <button onClick={submit} disabled={busy}
            className="flex-1 px-4 py-2 rounded-lg bg-accent text-bg font-medium text-[13px] hover:opacity-90 disabled:opacity-50 cursor-pointer">
            {busy ? '...' : '确认入账'}
          </button>
          <button onClick={onClose}
            className="px-4 py-2 rounded-lg border border-border text-text-dim hover:text-text hover:border-border-med text-[13px] cursor-pointer">
            取消
          </button>
        </div>
      </div>
    </div>
  )
}
