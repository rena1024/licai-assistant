import { useEffect, useState } from 'react'
import { fetchJSON } from '../hooks/useApi'

// 首次登录及后续未配置期间保持可见；配置可稍后完成，不阻断业务。
export default function NotificationStatus({ onOpen }) {
  const [pending, setPending] = useState(false)
  const [unread, setUnread] = useState(0)
  const [unavailable, setUnavailable] = useState(false)
  useEffect(() => {
    let live = true
    const load = async () => {
      try {
        const [channels, messages] = await Promise.all([fetchJSON('/api/notifications/channels'), fetchJSON('/api/notifications')])
        if (live) { setUnavailable(false); setPending(!channels.channels.some(c => c.active)); setUnread(messages.messages.filter(m => !m.read_at).length) }
      } catch { if (live) setUnavailable(true) }
    }
    load()
    const timer = setInterval(load, 30000)
    return () => { live = false; clearInterval(timer) }
  }, [])
  return <button onClick={onOpen} className="px-2 py-1 rounded-md border border-border text-xs" aria-label={`我的通知，${unread}条未读${pending ? '，外部渠道待配置' : ''}`}>
    {unavailable ? '通知状态未知' : pending ? '通知待配置' : '我的通知'}{unread > 0 ? ` (${unread}${unread === 50 ? '+' : ''})` : ''}
  </button>
}
