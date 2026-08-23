import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { apiRequest, dedupedFetch, clearInFlightCache } from '../../src/api/client'
import { getCockpitSummary } from '../../src/api/dashboard.api'
import { getProjects } from '../../src/api/projects.api'

const RESPONSE_BODY = { status: 'success', kpi: { total_projects: 6 } }

function mockFetchOk(body = RESPONSE_BODY) {
  globalThis.fetch = vi.fn(async () => new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } }))
}

beforeEach(() => {
  clearInFlightCache()
})

afterEach(() => {
  clearInFlightCache()
  vi.restoreAllMocks()
})

describe('dedupedFetch (GET coalescing)', () => {
  it('fires only one network call for two simultaneous identical GETs', async () => {
    mockFetchOk()
    const baseUrl = 'http://x'

    const a = getCockpitSummary(baseUrl)
    const b = getCockpitSummary(baseUrl)

    expect(a).toBe(b) // same Promise reference
    const [first, second] = await Promise.all([a, b])
    expect(first).toEqual(RESPONSE_BODY)
    expect(second).toEqual(RESPONSE_BODY)
    expect(globalThis.fetch).toHaveBeenCalledTimes(1)
  })

  it('does not coalesce different paths', async () => {
    mockFetchOk()
    const baseUrl = 'http://x'

    await Promise.all([
      getCockpitSummary(baseUrl),
      getProjects(baseUrl)
    ])
    expect(globalThis.fetch).toHaveBeenCalledTimes(2)
  })

  it('does not coalesce different auth tokens', async () => {
    mockFetchOk()
    const baseUrl = 'http://x'

    const a = dedupedFetch(baseUrl, '/a', { accessToken: 'token-A' })
    const b = dedupedFetch(baseUrl, '/a', { accessToken: 'token-B' })
    expect(a).not.toBe(b)
    await Promise.all([a, b])
    expect(globalThis.fetch).toHaveBeenCalledTimes(2)
  })

  it('releases the slot after settle so the next call hits the network', async () => {
    mockFetchOk()
    await getCockpitSummary('http://x')
    await getCockpitSummary('http://x')
    expect(globalThis.fetch).toHaveBeenCalledTimes(2)
  })

  it('clears the slot even when the underlying fetch rejects', async () => {
    globalThis.fetch = vi.fn(async () => { throw new Error('boom') })

    await expect(getCockpitSummary('http://x')).rejects.toThrow()
    // second call should not see a stale rejected promise
    await expect(getCockpitSummary('http://x')).rejects.toThrow()
    expect(globalThis.fetch).toHaveBeenCalledTimes(2)
  })
})

describe('apiRequest (mutating verbs)', () => {
  it('does not deduplicate POST requests', async () => {
    mockFetchOk({ status: 'success', reply: 'ok' })
    const baseUrl = 'http://x'

    await Promise.all([
      apiRequest(baseUrl, '/api/v1/executive/ai/chat', { method: 'POST', body: '{}' }),
      apiRequest(baseUrl, '/api/v1/executive/ai/chat', { method: 'POST', body: '{}' })
    ])
    expect(globalThis.fetch).toHaveBeenCalledTimes(2)
  })
})
