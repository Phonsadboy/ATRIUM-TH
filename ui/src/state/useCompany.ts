import { useRef, useSyncExternalStore } from 'react'
import { ApiClient } from '../contract/ApiClient'
import type { CompanyState } from '../contract/types'

// The UI always talks to the real ATRIUM backend over REST + WebSocket.
// Override the location with VITE_ATRIUM_API_URL; otherwise default to the
// local system service on :8787.
const apiUrl = (import.meta.env.VITE_ATRIUM_API_URL as string | undefined) ?? 'http://127.0.0.1:8787'
export const client = new ApiClient(apiUrl)

/** Whole-company snapshot. Reference is stable between backend pushes. */
export function useCompany(): CompanyState {
  return useSyncExternalStore(client.subscribe, client.getState)
}

/**
 * Selector hook with custom equality. Returns a cached value while the
 * underlying snapshot is unchanged, and avoids re-renders when the
 * selected slice is equal by `isEqual`.
 */
export function useSelector<T>(
  selector: (s: CompanyState) => T,
  isEqual: (a: T, b: T) => boolean = Object.is,
): T {
  const ref = useRef<{ state: CompanyState; value: T } | null>(null)
  const getSnapshot = () => {
    const state = client.getState()
    const prev = ref.current
    if (prev && prev.state === state) return prev.value
    const value = selector(state)
    if (prev && isEqual(prev.value, value)) {
      ref.current = { state, value: prev.value }
      return prev.value
    }
    ref.current = { state, value }
    return value
  }
  return useSyncExternalStore(client.subscribe, getSnapshot)
}

export function shallowArrayEqual<T>(a: readonly T[], b: readonly T[]): boolean {
  if (a === b) return true
  if (a.length !== b.length) return false
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false
  return true
}
