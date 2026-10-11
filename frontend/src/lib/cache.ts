/** SessionStorage-backed cache with TTL */
const PREFIX = 'nl_'

interface Entry<T> { v: T; exp: number }

export function cSet<T>(key: string, value: T, ttlMs: number) {
  try {
    sessionStorage.setItem(PREFIX + key, JSON.stringify({ v: value, exp: Date.now() + ttlMs }))
  } catch { /* quota exceeded — silently skip */ }
}

export function cGet<T>(key: string): T | null {
  try {
    const raw = sessionStorage.getItem(PREFIX + key)
    if (!raw) return null
    const entry: Entry<T> = JSON.parse(raw)
    if (Date.now() > entry.exp) { sessionStorage.removeItem(PREFIX + key); return null }
    return entry.v
  } catch { return null }
}

export function cDel(key: string) {
  try { sessionStorage.removeItem(PREFIX + key) } catch { /* ignore */ }
}

export const TTL = {
  SEARCH: 10 * 60_000,      // 10 min
  VIDEO_INFO: 30 * 60_000,  // 30 min
  LANGS: 30 * 60_000,       // 30 min
}
