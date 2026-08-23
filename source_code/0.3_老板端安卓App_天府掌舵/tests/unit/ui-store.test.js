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

  it('uses the stored serverBaseUrl and falls back to the default localhost URL', () => {
    localStorage.setItem(SERVER_URL_KEY, 'https://remote.example.com')
    setActivePinia(createPinia())
    const ui = useUiStore()
    expect(ui.serverBaseUrl).toBe('https://remote.example.com')

    localStorage.removeItem(SERVER_URL_KEY)
    setActivePinia(createPinia())
    const fallback = useUiStore()
    expect(fallback.serverBaseUrl).toMatch(/^http:\/\/127\.0\.0\.1:8922$/)
  })

  it('returns descriptive connection text per status and URL pattern', () => {
    const ui = useUiStore()
    ui.connectionStatus = 'offline'
    ui.serverBaseUrl = 'http://127.0.0.1:8922'
    expect(ui.connectionModeText).toContain('离线快照')

    ui.connectionStatus = 'connecting'
    expect(ui.connectionModeText).toContain('正在连接')

    ui.connectionStatus = 'connected'
    ui.serverBaseUrl = 'https://example.trycloudflare.com'
    expect(ui.connectionModeText).toContain('5G 远程加密直连')

    ui.connectionStatus = 'connected'
    ui.serverBaseUrl = 'http://127.0.0.1:8922'
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