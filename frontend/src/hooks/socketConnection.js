// 返回一个完整的清理函数：覆盖每一代连接、心跳和待执行的重连。
export function connectSocket(url, onMessage) {
  let stopped = false
  let socket
  let heartbeat
  let reconnect
  let delay = 1000
  const connect = () => {
    if (stopped) return
    const current = new WebSocket(url)
    socket = current
    current.onopen = () => { delay = 1000 }
    current.onmessage = (event) => {
      if (stopped || socket !== current) return
      try {
        const message = JSON.parse(event.data)
        if (!['heartbeat', 'pong'].includes(message.type)) onMessage(message)
      } catch {}
    }
    current.onclose = () => {
      clearInterval(heartbeat)
      clearTimeout(reconnect)
      if (stopped) return
      reconnect = setTimeout(connect, delay)
      delay = Math.min(delay * 2, 30000)
    }
    current.onerror = () => current.close()
    heartbeat = setInterval(() => {
      if (!stopped && current.readyState === WebSocket.OPEN) current.send(JSON.stringify({ type: 'ping' }))
    }, 25000)
  }
  connect()
  return () => {
    stopped = true
    clearTimeout(reconnect)
    clearInterval(heartbeat)
    if (socket) {
      socket.onopen = socket.onmessage = socket.onclose = socket.onerror = null
      socket.close()
    }
  }
}
