import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useUiStore } from '../../src/stores/ui.store'

const SERVER_URL_KEY = 'cdjg_server_url'
const SETTINGS_KEY = 'cdjg_executive_settings'

describe('UI store', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
  })

  afterEach(() => {
    localStorage.clear()
    vi.unstubAllGlobals()
    vi.unstubAllEnvs()
  })

  it('defaults offlineCacheEnabled to true when no settings are stored', () => {
    const ui = useUiStore()
    expect(ui.offlineCacheEnabled).toBe(true)
  })

  it('toggles offlineCacheEnabled and persists it through persistSettings', async () => {
    const ui = useUiStore()
    ui.offlineCacheEnabled = false
    await vi.waitFor(() => {
      const stored = JSON.parse(localStorage.getItem(SETTINGS_KEY) || '{}')
      expect(stored.offlineCacheEnabled).toBe(false)
    })
    expect(localStorage.getItem(SERVER_URL_KEY)).toBeTruthy()

    ui.offlineCacheEnabled = true
    await vi.waitFor(() => {
      const stored = JSON.parse(localStorage.getItem(SETTINGS_KEY) || '{}')
      expect(stored.offlineCacheEnabled).toBe(true)
    })
  })

  it('uses the stored serverBaseUrl and falls back to the Tax 8921 loopback URL', () => {
    localStorage.setItem(SERVER_URL_KEY, 'https://remote.example.com')
    setActivePinia(createPinia())
    const ui = useUiStore()
    expect(ui.serverBaseUrl).toBe('https://remote.example.com')

    localStorage.removeItem(SERVER_URL_KEY)
    setActivePinia(createPinia())
    const fallback = useUiStore()
    expect(fallback.serverBaseUrl).toBe(
      import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8922'
    )
  })

  it('falls back to port 8921 when VITE_API_BASE_URL is not configured', async () => {
    vi.resetModules()
    vi.stubEnv('VITE_API_BASE_URL', '')
    const { useUiStore: useFreshUiStore } = await import('../../src/stores/ui.store')

    setActivePinia(createPinia())
    const fallback = useFreshUiStore()

    expect(fallback.serverBaseUrl).toBe('http://127.0.0.1:8922')
  })

  it('allows localhost and 127.0.0.1 plain HTTP in production server policy', async () => {
    vi.resetModules()
    vi.stubEnv('DEV', false)
    vi.stubEnv('PROD', true)
    const { getUrlViolationReason, isAllowedServerUrl } = await import('../../src/config/server-policy')

    for (const url of ['http://127.0.0.1:8921', 'http://localhost:8921']) {
      expect(getUrlViolationReason(url)).toBeNull()
      expect(isAllowedServerUrl(url)).toBe(true)
    }
  })

  it('ignores a persisted private-LAN URL when the page runs on loopback', () => {
    vi.stubGlobal('location', { hostname: '127.0.0.1' })
    localStorage.setItem(SERVER_URL_KEY, 'http://192.168.1.3:8922')

    const ui = useUiStore()

    expect(ui.serverBaseUrl).toBe(
      import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8922'
    )
  })

  it('preserves a persisted private-LAN URL when the page runs on a LAN host', () => {
    vi.stubGlobal('location', { hostname: '192.168.10.36' })
    const storedUrl = 'http://192.168.1.3:8922'
    localStorage.setItem(SERVER_URL_KEY, storedUrl)

    const ui = useUiStore()

    expect(ui.serverBaseUrl).toBe(storedUrl)
  })

  it('preserves persisted HTTPS and loopback URLs on a loopback page', () => {
    vi.stubGlobal('location', { hostname: 'localhost' })
    localStorage.setItem(SERVER_URL_KEY, 'https://api.example.com')
    expect(useUiStore().serverBaseUrl).toBe('https://api.example.com')

    localStorage.clear()
    localStorage.setItem(SERVER_URL_KEY, 'http://127.0.0.1:8922')
    setActivePinia(createPinia())
    expect(useUiStore().serverBaseUrl).toBe('http://127.0.0.1:8922')
  })

  it('keeps the store usable when Web Storage reads and writes throw', () => {
    vi.spyOn(localStorage, 'getItem').mockImplementation(() => {
      throw new Error('storage unavailable')
    })
    vi.spyOn(localStorage, 'setItem').mockImplementation(() => {
      throw new Error('storage unavailable')
    })

    const ui = useUiStore()

    expect(ui.serverBaseUrl).toBe(
      import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8922'
    )
    expect(() => ui.persistSettings()).not.toThrow()
  })

  it('returns descriptive connection text per status and URL pattern', () => {
    const ui = useUiStore()
    ui.connectionStatus = 'offline'
    ui.serverBaseUrl = 'http://127.0.0.1:8921'
    expect(ui.connectionModeText).toContain('离线快照')

    ui.connectionStatus = 'connecting'
    expect(ui.connectionModeText).toContain('正在连接')

    ui.connectionStatus = 'connected'
    ui.serverBaseUrl = 'https://example.trycloudflare.com'
    expect(ui.connectionModeText).toContain('5G 远程加密直连')

    ui.connectionStatus = 'connected'
    ui.serverBaseUrl = 'http://127.0.0.1:8921'
    expect(ui.connectionModeText).toContain('本地/局域网直连模式')
  })

  it('reflects privacyMode changes reactively', () => {
    const ui = useUiStore()
    expect(ui.privacyMode).toBe(false)
    ui.privacyMode = true
    expect(ui.privacyMode).toBe(true)
    ui.privacyMode = false
    expect(ui.privacyMode).toBe(false)
  })
})
