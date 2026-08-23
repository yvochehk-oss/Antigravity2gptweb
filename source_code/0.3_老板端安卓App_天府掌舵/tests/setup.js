import { afterEach } from 'vitest'

function createMemoryStorage() {
  const values = new Map()
  return {
    get length() {
      return values.size
    },
    clear: () => values.clear(),
    getItem: key => values.get(String(key)) ?? null,
    key: index => [...values.keys()][index] ?? null,
    removeItem: key => values.delete(String(key)),
    setItem: (key, value) => values.set(String(key), String(value))
  }
}

// Node 25 exposes an incomplete localStorage when no backing file is configured.
// Keep tests deterministic on both Node 22 CI and newer local runtimes.
if (typeof globalThis.localStorage?.clear !== 'function') {
  const localStorage = createMemoryStorage()
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: localStorage })
  Object.defineProperty(window, 'localStorage', { configurable: true, value: localStorage })
}

if (typeof window !== 'undefined') {
  Object.defineProperty(window, 'scrollTo', { configurable: true, writable: true, value: () => {} })
  Object.defineProperty(globalThis, 'scrollTo', { configurable: true, writable: true, value: () => {} })
}

afterEach(() => {
  localStorage.clear()
  sessionStorage.clear()
})
