const DEFAULT_TIMEOUT = 12_000

export class ApiError extends Error {
  constructor(message, { status = 0, cause } = {}) {
    super(message, { cause })
    this.name = 'ApiError'
    this.status = status
  }
}

export function normalizeBaseUrl(value) {
  return String(value || '').trim().replace(/\/$/, '')
}

/**
 * In-flight GET requests keyed by ``method:url:authBucket``. When a second
 * identical GET fires while the first is still pending, the second caller
 * receives the same Promise — no duplicate network hit. Mutating verbs
 * (POST/PUT/PATCH/DELETE) bypass the map so side-effecting operations are
 * never coalesced.
 */
const inFlightGets = new Map()

function dedupKey(baseUrl, path, options) {
  const method = (options.method || 'GET').toUpperCase()
  // Bucket by token presence AND token identity so different users don't
  // accidentally share a cached response.
  const authBucket = options.accessToken
    ? `auth:${hashToken(options.accessToken)}`
    : 'anon'
  return `${method}::${normalizeBaseUrl(baseUrl)}::${path}::${authBucket}`
}

function hashToken(token) {
  // Cheap non-cryptographic hash; we only need collision resistance
  // within a single user session, not adversarial uniqueness.
  let hash = 5381
  for (let i = 0; i < token.length; i += 1) {
    hash = (hash * 33) ^ token.charCodeAt(i)
  }
  return (hash >>> 0).toString(36)
}

export function dedupedFetch(baseUrl, path, options = {}) {
  const method = (options.method || 'GET').toUpperCase()
  if (method !== 'GET') return null // only deduplicate safe GETs

  const key = dedupKey(baseUrl, path, options)
  if (inFlightGets.has(key)) return inFlightGets.get(key)
  const promise = apiRequest(baseUrl, path, options).finally(() => {
    inFlightGets.delete(key)
  })
  inFlightGets.set(key, promise)
  return promise
}

/** Exposed for tests + ops tooling. */
export function clearInFlightCache() {
  inFlightGets.clear()
}

export async function apiRequest(baseUrl, path, options = {}) {
  const controller = new AbortController()
  const {
    accessToken,
    headers,
    timeout = DEFAULT_TIMEOUT,
    signal: callerSignal,
    ...requestOptions
  } = options
  const timeoutId = window.setTimeout(() => controller.abort(), timeout)

  let externalAbortHandler
  if (callerSignal) {
    if (callerSignal.aborted) {
      controller.abort(callerSignal.reason)
    } else {
      externalAbortHandler = () => controller.abort(callerSignal.reason)
      callerSignal.addEventListener('abort', externalAbortHandler)
    }
  }

  try {
    const response = await fetch(`${normalizeBaseUrl(baseUrl)}${path}`, {
      ...requestOptions,
      headers: {
        Accept: 'application/json',
        ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
        ...headers
      },
      signal: controller.signal
    })

    if (!response.ok) {
      if (response.status === 401 && !options._retry) {
        try {
          const { useAuthStore } = await import('../stores/auth.store')
          const auth = useAuthStore()
          const refreshed = await auth.refreshSession()
          if (refreshed && auth.session?.accessToken) {
            return await apiRequest(baseUrl, path, {
              ...options,
              _retry: true,
              accessToken: auth.session.accessToken
            })
          }
        } catch {
          // fallback to regular error
        }
      }

      const hint = (
        response.status === 401 ? '（会话已过期，请重新登录）'
        : response.status === 403 ? '（当前角色无权访问）'
        : response.status === 404 ? '（资源不存在）'
        : ''
      )
      throw new ApiError(`API 请求失败 (${response.status})${hint}`, {
        status: response.status,
        cause: { url: path, method: requestOptions.method || 'GET' }
      })
    }

    if (response.status === 204) return null
    return await response.json()
  } catch (error) {
    if (error instanceof ApiError) throw error
    const isCallerAborted = callerSignal?.aborted
    const message = (
      isCallerAborted ? 'API 请求已取消'
      : error?.name === 'AbortError' ? 'API 请求超时'
      : '无法连接数据服务'
    )
    throw new ApiError(message, { cause: error })
  } finally {
    window.clearTimeout(timeoutId)
    if (callerSignal && externalAbortHandler) {
      callerSignal.removeEventListener('abort', externalAbortHandler)
    }
  }
}
