import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  DEFAULT_SNAPSHOT_MAX_AGE_MS,
  readSnapshot,
  sanitizeSnapshot,
  saveSnapshot
} from '../../src/offline/snapshot'

describe('offline snapshot sanitization + encryption', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-08-22T10:00:00.000Z'))
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('removes sensitive fields from a flat company object', () => {
    const sanitized = sanitizeSnapshot({
      company_name: '天府建工第七分公司',
      uscc: '91510100MA5XYZ12345',
      legal_representative: '张三',
      tax_no: '91510100123456789X',
      bank_account: '6222021234567890123'
    })
    expect(sanitized).toEqual({ company_name: '天府建工第七分公司' })
  })

  it('keeps non-sensitive business fields untouched', () => {
    const sanitized = sanitizeSnapshot({
      company_name: '天府一建',
      revenue: 12_345,
      active: true,
      tags: ['总承包', 'A类']
    })
    expect(sanitized).toEqual({
      company_name: '天府一建',
      revenue: 12_345,
      active: true,
      tags: ['总承包', 'A类']
    })
  })

  it('encrypts on save and decrypts back to the same value on read', async () => {
    const payload = { revenue: 12_345, projects: [{ id: 1 }, { id: 2 }] }
    const saved = await saveSnapshot('cockpit', payload)
    expect(saved).toBe(true)

    const stored = JSON.parse(localStorage.getItem('cdjg_snapshot_cockpit'))
    expect(stored.cipher).toBeTypeOf('string')
    expect(stored.cipher).not.toContain('12345')

    expect(await readSnapshot('cockpit', null)).toEqual(payload)
  })

  it('returns the fallback when the snapshot has expired', async () => {
    await saveSnapshot('cockpit', { revenue: 1 })
    vi.advanceTimersByTime(DEFAULT_SNAPSHOT_MAX_AGE_MS + 1)

    const fallback = { source: 'live' }
    expect(await readSnapshot('cockpit', fallback)).toBe(fallback)
    expect(localStorage.getItem('cdjg_snapshot_cockpit')).toBeNull()
  })

  it('returns the fallback when storage is unavailable', async () => {
    const descriptor = Object.getOwnPropertyDescriptor(window, 'localStorage')
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      get: () => undefined
    })

    try {
      const fallback = { source: 'memory' }
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
})