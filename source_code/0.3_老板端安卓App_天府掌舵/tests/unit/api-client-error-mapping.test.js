import { afterEach, describe, expect, it, vi } from 'vitest'
import { apiRequest, normalizeBaseUrl } from '../../src/api/client'

describe('apiRequest error mapping', () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('maps AbortError to a localized timeout ApiError', async () => {
    vi.stubGlobal('fetch', vi.fn().mockImplementation((_, options) => new Promise((_resolve, reject) => {
      options.signal.addEventListener('abort', () => {
        const err = new Error('aborted')
        err.name = 'AbortError'
        reject(err)
      })
    })))

    const promise = apiRequest('', '/slow', { timeout: 5 })
    // 等真实时间触发 setTimeout
    await expect(promise).rejects.toMatchObject({
      name: 'ApiError',
      status: 0,
      message: expect.stringContaining('超时')
    })
  })

  it('returns ApiError with status for HTTP 4xx/5xx responses', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 503 }))

    await expect(apiRequest('', '/down')).rejects.toMatchObject({
      name: 'ApiError',
      status: 503,
      message: 'API 请求失败 (503)'
    })
  })

  it('trims and normalizes trailing slashes via normalizeBaseUrl', () => {
    expect(normalizeBaseUrl('https://api.example.com/')).toBe('https://api.example.com')
    expect(normalizeBaseUrl('  https://x.com/api/  ')).toBe('https://x.com/api')
    expect(normalizeBaseUrl(null)).toBe('')
  })

  it('omits options keys that are not part of fetch signature', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 204 })
    vi.stubGlobal('fetch', fetchMock)

    await apiRequest('https://x.com', '/no-content', { accessToken: 't', timeout: 100 })
    const [, requestOptions] = fetchMock.mock.calls[0]
    expect(requestOptions).not.toHaveProperty('accessToken')
    expect(requestOptions).not.toHaveProperty('timeout')
  })

  it('does not leak the underlying network error class', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('socket closed')))

    await expect(apiRequest('', '/x')).rejects.toMatchObject({
      name: 'ApiError',
      message: '无法连接数据服务',
      status: 0
    })
  })
})