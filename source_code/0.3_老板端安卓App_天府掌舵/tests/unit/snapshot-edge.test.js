import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import { clearSnapshots, readSnapshot, saveSnapshot } from '../../src/offline/snapshot'

describe('snapshot edge cases', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-08-22T08:30:00.000Z'))
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('falls back when storage is unavailable', async () => {
    const descriptor = Object.getOwnPropertyDescriptor(window, 'localStorage')
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      get: () => undefined
    })

    try {
      const fallback = { source: 'default' }
      expect(await readSnapshot('cockpit', fallback)).toBe(fallback)
      expect(await saveSnapshot('cockpit', { anything: 1 })).toBe(false)
    } finally {
      if (descriptor) {
        Object.defineProperty(window, 'localStorage', descriptor)
      } else {
        delete window.localStorage
      }
    }
  })

  it('expires old snapshots and clears the key', async () => {
    await saveSnapshot('cockpit', { revenue: 1 })

    vi.advanceTimersByTime(24 * 60 * 60 * 1000 + 1)

    const fallback = { source: 'live' }
    expect(await readSnapshot('cockpit', fallback)).toBe(fallback)
    expect(localStorage.getItem('cdjg_snapshot_cockpit')).toBeNull()
  })

  it('returns fallback for malformed cipher payload', async () => {
    const savedAt = new Date().toISOString()
    localStorage.setItem(
      'cdjg_snapshot_cockpit',
      JSON.stringify({ cipher: 'garbage', savedAt })
    )

    const fallback = { source: 'memory' }
    expect(await readSnapshot('cockpit', fallback)).toBe(fallback)
  })

  it('handles storage quota exceeded gracefully', async () => {
    // jsdom's Storage methods live on its prototype and cannot be reliably
    // replaced by assigning an own property on the Storage instance. Node
    // 25's setup fallback is a plain memory-storage object, though, so use
    // the instance only when the prototype does not provide setItem.
    const storageTarget = typeof Object.getPrototypeOf(localStorage).setItem === 'function'
      ? Object.getPrototypeOf(localStorage)
      : localStorage
    const setItemSpy = vi.spyOn(storageTarget, 'setItem').mockImplementation(() => {
      throw new Error('QuotaExceededError')
    })

    try {
      expect(await saveSnapshot('cockpit', { anything: 1 })).toBe(false)
    } finally {
      setItemSpy.mockRestore()
    }
  })

  it('clearSnapshots removes only prefixed keys', async () => {
    await saveSnapshot('cockpit', { a: 1 })
    localStorage.setItem('other_app_setting', 'keep-me')

    clearSnapshots()
    expect(localStorage.getItem('cdjg_snapshot_cockpit')).toBeNull()
    expect(localStorage.getItem('other_app_setting')).toBe('keep-me')
  })
})
