import { useEffect, useRef } from 'react'
import { connectSocket } from './socketConnection'
import { SESSION_ENDED } from './sessionRuntime'

export function useWebSocket(onMessage) {
  const onMessageRef = useRef(onMessage)
  useEffect(() => { onMessageRef.current = onMessage }, [onMessage])
  useEffect(() => {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const stop = connectSocket(`${protocol}//${location.host}/ws`, message => onMessageRef.current?.(message))
    window.addEventListener(SESSION_ENDED, stop)
    return () => {
      window.removeEventListener(SESSION_ENDED, stop)
      stop()
    }
  }, [])
}
