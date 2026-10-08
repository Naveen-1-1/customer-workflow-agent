import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useDeps } from '@/api/context'

export type StreamStatus = 'connecting' | 'open' | 'closed'

const CLOSED = 2 // EventSource.CLOSED

// Subscribes to a server-sent event stream. Every event is a full snapshot, so
// reconnecting (which EventSource does by itself) needs no replay.
export function useEventSource(
  url: string | null,
  handlers: Record<string, (data: unknown) => void>,
): StreamStatus {
  const { openEvents } = useDeps()
  const [status, setStatus] = useState<StreamStatus>('connecting')
  const handlersRef = useRef(handlers)
  useLayoutEffect(() => {
    handlersRef.current = handlers
  })

  useEffect(() => {
    if (!url) return
    const es = openEvents(url)
    es.onopen = () => setStatus('open')
    es.onerror = () => setStatus(es.readyState === CLOSED ? 'closed' : 'connecting')
    const names = Object.keys(handlersRef.current)
    const listeners = names.map((name) => {
      const listener = (e: Event) => {
        setStatus('open')
        handlersRef.current[name]?.(JSON.parse((e as MessageEvent).data))
      }
      es.addEventListener(name, listener)
      return [name, listener] as const
    })
    return () => {
      listeners.forEach(([name, l]) => es.removeEventListener(name, l))
      es.close()
    }
  }, [url, openEvents])

  return status
}
