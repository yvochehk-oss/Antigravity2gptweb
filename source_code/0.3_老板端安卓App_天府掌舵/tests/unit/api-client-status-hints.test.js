import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, apiRequest, normalizeBaseUrl } from '../../src/api/client'

describe('apiRequest status-specific messages', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('annotates 401 with a session-expiry hint', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 401 }))
    await expect(apiRequest('', '/me')).rejects.toMatchObject({
      name: 'ApiError',
      status: 401,
      message: expect.stringContaining('会话已过期')
    })
  })

  it('annotates 403 with a permission-denied hint', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 403 }))
    await expect(apiRequest('', '/secret')).rejects.toMatchObject({
      name: 'ApiError',
      status: 403,
      message: expect.stringContaining('当前角色无权访问')
    })
  })

  it('annotates 404 with a not-found hint', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 404 }))
    await expect(apiRequest('', '/missing')).rejects.toMatchObject({
      name: 'ApiError',
      status: 404,
      message: expect.stringContaining('资源不存在')
    })
  })

  it('does not annotate generic 4xx/5xx beyond the status code', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 503 }))
    await expect(apiRequest('', '/down')).rejects.toMatchObject({
      name: 'ApiError',
      status: 503,
      message: 'API 请求失败 (503)'
    })
  })

  it('preserves the original URL and method on the cause object', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 401 }))
    const err = await apiRequest('https://x.com', '/admin/token', { method: 'POST' }).catch(e => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err.cause).toMatchObject({ url: '/admin/token', method: 'POST' })
  })
})

describe('normalizeBaseUrl', () => {
  it('passes through trim/trailing-slash pipeline', () => {
    expect(normalizeBaseUrl('  https://x.com/api/  ')).toBe('https://x.com/api')
    expect(normalizeBaseUrl('http://1.2.3.4:8922/')).toBe('http://1.2.3.4:8922')
  })

  it('returns empty string for nullish input', () => {
    expect(normalizeBaseUrl(null)).toBe('')
    expect(normalizeBaseUrl(undefined)).toBe('')
    expect(normalizeBaseUrl('')).toBe('')
  })
})
