import { ApiError, apiRequest, normalizeBaseUrl } from '../../src/api/client'

describe('API client', () => {
  it('normalizes an optional trailing slash', () => {
    expect(normalizeBaseUrl(' https://api.example.com/ ')).toBe('https://api.example.com')
    expect(normalizeBaseUrl()).toBe('')
  })

  it('builds the request URL and merges JSON accept headers', async () => {
    const payload = { status: 'ok' }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: vi.fn().mockResolvedValue(payload)
    }))

    await expect(apiRequest('https://api.example.com/', '/health', {
      accessToken: 'test-token',
      timeout: 5_000
    })).resolves.toEqual(payload)

    expect(fetch).toHaveBeenCalledWith('https://api.example.com/health', expect.objectContaining({
      headers: {
        Accept: 'application/json',
        Authorization: 'Bearer test-token'
      },
      signal: expect.any(AbortSignal)
    }))
    const [, requestOptions] = fetch.mock.calls[0]
    expect(requestOptions).not.toHaveProperty('timeout')
    expect(requestOptions).not.toHaveProperty('accessToken')
  })

  it('returns null for an empty success response', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 204 }))

    await expect(apiRequest('', '/no-content')).resolves.toBeNull()
  })

  it('preserves the HTTP status in a typed API error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 403 }))

    await expect(apiRequest('', '/forbidden')).rejects.toMatchObject({
      name: 'ApiError',
      message: 'API 请求失败 (403)（当前角色无权访问）',
      status: 403
    })
  })

  it('wraps network errors without exposing implementation details', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('socket closed')))

    const request = apiRequest('', '/unavailable')
    await expect(request).rejects.toBeInstanceOf(ApiError)
    await expect(request).rejects.toMatchObject({ message: '无法连接数据服务', status: 0 })
  })

  it('respects caller-supplied AbortSignal', async () => {
    const controller = new AbortController()
    vi.stubGlobal('fetch', vi.fn().mockImplementation((_, opts) => {
      return new Promise((_, reject) => {
        opts.signal.addEventListener('abort', () => {
          const err = new Error('aborted')
          err.name = 'AbortError'
          reject(err)
        })
      })
    }))

    const req = apiRequest('', '/long-op', { signal: controller.signal })
    controller.abort()
    await expect(req).rejects.toMatchObject({ message: 'API 请求已取消' })
  })
})
