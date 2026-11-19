export default function SaveStatus({ busy = false, message = '', error = '' }) {
  const text = busy ? '保存中…' : error || message
  return <span role={error && !busy ? 'alert' : 'status'} aria-live="polite" aria-atomic="true"
    className={`inline-flex items-center gap-1.5 text-[12px] leading-5 break-words ${busy ? 'text-accent' : error ? 'text-bear' : 'text-bull'}`}>
    {text && <><span aria-hidden="true">{busy ? '…' : error ? '!' : '✓'}</span>{text}</>}
  </span>
}
