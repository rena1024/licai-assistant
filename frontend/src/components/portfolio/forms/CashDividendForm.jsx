import React from 'react'
import { fetchJSON } from '../../../hooks/useApi'

export default function CashDividendForm({ assetId, onCancel, onSaved }) {
  const [date, setDate] = React.useState('')
  const [amount, setAmount] = React.useState('')
  const [note, setNote] = React.useState('')
  const [saving, setSaving] = React.useState(false)
  const [error, setError] = React.useState('')
  const submitting = React.useRef(false)
  const amountRef = React.useRef(null)
  const prefix = React.useId()
  // 使用本地日期，避免 UTC 日期在凌晨与用户看到的今天不一致。
  const today = new Date().toLocaleDateString('sv-SE')

  const submit = async event => {
    event.preventDefault()
    if (submitting.current) return
    const value = Number(amount)
    if (!Number.isFinite(value) || value <= 0) {
      setError('到账金额必须为正数')
      return
    }
    if (!date || date > today) {
      setError('请填写不晚于今天的真实到账日期')
      return
    }
    const keepOpen = event.nativeEvent.submitter?.value === 'continue'
    submitting.current = true
    setSaving(true)
    setError('')
    try {
      await fetchJSON(`/api/assets/${assetId}/dividends`, {
        method: 'POST', body: JSON.stringify({ amount: value, trade_date: date, note: note.trim() }),
      })
    } catch (e) {
      setError(e.message)
      return
    } finally {
      submitting.current = false
      setSaving(false)
    }
    setAmount('')
    setNote('')
    onSaved(keepOpen)
    if (keepOpen) amountRef.current?.focus()
  }

  const inputClass = 'w-full bg-bg border border-border rounded px-2 py-1.5 text-[12px] text-text min-w-0'
  const buttonClass = 'px-2 py-1 rounded text-[11px] border cursor-pointer disabled:opacity-50 disabled:cursor-default'
  return (
    <form onSubmit={submit} className="bg-surface-3 border border-border-subtle rounded-md p-3 mb-3" aria-label="补录现金分红">
      <p className="text-[11px] text-text-dim m-0 mb-3">按实际到账日期补录，金额计入已实现收益。红利再投资不适用此入口。</p>
      <fieldset disabled={saving} className="border-0 p-0 m-0 min-w-0">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <label htmlFor={`${prefix}-date`} className="text-[11px] text-text-dim">
            到账日期
            <input id={`${prefix}-date`} type="date" required max={today} value={date}
              onChange={e => setDate(e.target.value)} className={`${inputClass} mt-1`} autoFocus />
          </label>
          <label htmlFor={`${prefix}-amount`} className="text-[11px] text-text-dim">
            到账金额（元）
            <input ref={amountRef} id={`${prefix}-amount`} type="number" required min="0.01" step="0.01" value={amount}
              onChange={e => setAmount(e.target.value)} className={`${inputClass} mt-1`} placeholder="实际到账金额" />
          </label>
        </div>
        <label htmlFor={`${prefix}-note`} className="block text-[11px] text-text-dim mt-3">
          备注（可选）
          <input id={`${prefix}-note`} value={note} onChange={e => setNote(e.target.value)} className={`${inputClass} mt-1`} />
        </label>
      </fieldset>
      {error && <div role="alert" className="text-bear-bright text-[11px] mt-2">{error}</div>}
      <div className="flex flex-wrap justify-end gap-2 mt-3">
        <button type="button" onClick={onCancel} disabled={saving} className={`${buttonClass} border-border text-text-dim`}>取消</button>
        <button type="submit" value="continue" disabled={saving} className={`${buttonClass} border-accent/60 text-accent`}>保存并继续</button>
        <button type="submit" value="save" disabled={saving} className={`${buttonClass} border-accent bg-accent/10 text-accent`}>{saving ? '保存中…' : '保存'}</button>
      </div>
    </form>
  )
}
