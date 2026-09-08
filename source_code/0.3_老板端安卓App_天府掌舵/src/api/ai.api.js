import { normalizeBaseUrl } from './client'

const DEFAULT_TIMEOUT = 30_000

export async function askExecutiveCopilot(baseUrl, message, accessToken, projectId = null) {
  const { apiRequest } = await import('./client')
  const payload = { message }
  if (projectId !== null && projectId !== undefined) {
    payload.project_id = projectId
  }
  return apiRequest(baseUrl, '/api/v1/executive/ai/chat', {
    method: 'POST',
    accessToken,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  })
}

export class CopilotStreamAbortError extends Error {
  constructor() {
    super('aborted')
    this.name = 'CopilotStreamAbortError'
  }
}

/**
 * Stream the executive copilot reply (SSE). Returns an async iterator of:
 *
 *   { type: 'delta',     data: string }
 *   { type: 'citations', data: Array<{title, url}> }
 *   { type: 'meta',      data: { data_source: 'rag' | 'unavailable' | string } }
 *   { type: 'done',      data: { timestamp: number } }
 *   { type: 'error',     data: string }
 *
 * Pass an AbortSignal to stop mid-stream (e.g. user clicks "停止生成").
 * The iterator resolves cleanly on abort and will not throw a network
 * error in that case.
 */
export async function* streamExecutiveCopilot(baseUrl, message, accessToken, signal, projectId = null, options = {}) {
  const url = `${normalizeBaseUrl(baseUrl)}/api/v1/executive/ai/chat/stream`
  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), DEFAULT_TIMEOUT)

  // Chain the user-provided signal into the controller we own.
  let externalAbortHandler
  if (signal) {
    if (signal.aborted) {
      controller.abort()
    } else {
      externalAbortHandler = () => controller.abort(signal.reason)
      signal.addEventListener('abort', externalAbortHandler)
    }
  }

  const payload = { message }
  if (projectId !== null && projectId !== undefined) {
    payload.project_id = projectId
  }

  try {
    const response = await fetch(url, {
      method: 'POST',
      headers: {
        Accept: 'text/event-stream',
        'Content-Type': 'application/json',
        ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {})
      },
      body: JSON.stringify(payload),
      signal: controller.signal
    })

    if (!response.ok || !response.body) {
      if (response.status === 401 && !options?._retry) {
        try {
          const { useAuthStore } = await import('../stores/auth.store')
          const auth = useAuthStore()
          const refreshed = await auth.refreshSession()
          if (refreshed && auth.session?.accessToken) {
            yield* streamExecutiveCopilot(baseUrl, message, auth.session.accessToken, signal, projectId, { _retry: true })
            return
          }
        } catch {
          // fallback to error
        }
      }
      yield { type: 'error', data: `智策流式通道失败 (${response.status})` }
      return
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder('utf-8')
    let buffer = ''

    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      const { frames, remaining } = splitSseFrames(buffer)
      buffer = remaining

      for (const frame of frames) {
        const parsed = parseSseFrame(frame)
        if (!parsed) continue
        if (parsed.event === 'delta') {
          yield { type: 'delta', data: parsed.data }
        } else if (parsed.event === 'citations') {
          yield { type: 'citations', data: parsed.data }
        } else if (parsed.event === 'meta') {
          yield { type: 'meta', data: parsed.data }
        } else if (parsed.event === 'done') {
          yield { type: 'done', data: parsed.data }
        }
      }
    }
  } catch (error) {
    if (error?.name === 'AbortError') return // clean abort — no error yielded
    yield { type: 'error', data: error?.message || '智策流式读取失败' }
  } finally {
    clearTimeout(timeoutId)
    if (signal && externalAbortHandler) signal.removeEventListener('abort', externalAbortHandler)
  }
}

/**
 * SSE frame delimiter: blank line (\n\n or \r\n\r\n).
 * Split frames keeping order.
 */
function splitSseFrames(text) {
  const frames = []
  let remaining = text
  while (remaining.length > 0) {
    // Try CRLF first, then LF
    const crlfIdx = remaining.indexOf('\r\n\r\n')
    const lfIdx = remaining.indexOf('\n\n')
    let frameEnd
    if (crlfIdx !== -1 && (lfIdx === -1 || crlfIdx < lfIdx)) {
      frameEnd = crlfIdx
    } else if (lfIdx !== -1) {
      frameEnd = lfIdx
    } else {
      break
    }
    frames.push(remaining.slice(0, frameEnd))
    const delimiterLen = remaining[frameEnd] === '\r' ? 4 : 2
    remaining = remaining.slice(frameEnd + delimiterLen)
  }
  return { frames, remaining }
}

function parseSseFrame(frame) {
  let event = 'message'
  const dataLines = []
  for (const line of frame.split(/\r?\n/)) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
  }
  if (dataLines.length === 0) return null
  const dataRaw = dataLines.join('\n')
  try {
    return { event, data: JSON.parse(dataRaw) }
  } catch {
    return { event, data: dataRaw }
  }
}
