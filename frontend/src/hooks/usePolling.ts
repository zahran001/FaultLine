import { useEffect, useRef, useState } from 'react'

// Polling primitive (the decided data-fetching approach — NO WebSocket/SSE).
// Re-invokes `fetcher` every `intervalMs`. State lives only in React (no storage APIs).
//
// `key` lets a caller force a clean restart (e.g. selecting a different vehicle):
// when it changes, the previous data is dropped so a stale vehicle's readings never
// flash under the newly-selected one.
export interface PollState<T> {
  data: T | null
  error: string | null
  loading: boolean
}

export function usePolling<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  intervalMs: number,
  enabled = true,
  key?: string,
): PollState<T> {
  const [state, setState] = useState<PollState<T>>({
    data: null,
    error: null,
    loading: true,
  })

  // Keep the latest fetcher without making it a dependency (it's an inline closure
  // that changes every render); the effect re-runs only on interval/enabled/key.
  const fetcherRef = useRef(fetcher)
  fetcherRef.current = fetcher

  useEffect(() => {
    if (!enabled) return

    // New key ⇒ fresh load: clear prior vehicle's data so it can't flash.
    setState({ data: null, error: null, loading: true })

    let cancelled = false
    let timer: ReturnType<typeof setTimeout>
    let controller: AbortController

    const tick = async () => {
      controller = new AbortController()
      try {
        const data = await fetcherRef.current(controller.signal)
        if (!cancelled) setState({ data, error: null, loading: false })
      } catch (err) {
        if (!cancelled && (err as Error).name !== 'AbortError') {
          setState((s) => ({
            data: s.data,
            error: (err as Error).message,
            loading: false,
          }))
        }
      } finally {
        if (!cancelled) timer = setTimeout(tick, intervalMs)
      }
    }

    tick()
    return () => {
      cancelled = true
      clearTimeout(timer)
      controller?.abort()
    }
  }, [intervalMs, enabled, key])

  return state
}
