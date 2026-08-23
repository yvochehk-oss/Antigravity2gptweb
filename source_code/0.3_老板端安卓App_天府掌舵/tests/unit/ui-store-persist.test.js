import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'
import { useUiStore } from '../../src/stores/ui.store'

const SERVER_URL_KEY = 'cdjg_server_url'
const SETTINGS_KEY = 'cdjg_executive_settings'

describe('UI store persistSettings watch', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
  })

  it('persists serverBaseUrl whenever it changes', async () => {
    const ui = useUiStore()
    ui.serverBaseUrl = 'https://remote.example.com'

    await new Promise(resolve => setTimeout(resolve, 0))

    expect(localStorage.getItem(SERVER_URL_KEY)).toBe('https://remote.example.com')
  })

  it('persists privacyMode toggles through the same pipeline', async () => {
    const ui = useUiStore()
    ui.privacyMode = true

    await new Promise(resolve => setTimeout(resolve, 0))

    const stored = JSON.parse(localStorage.getItem(SETTINGS_KEY))
    expect(stored.privacyMode).toBe(true)
  })

  it('rehydrates privacyMode from storage on a fresh Pinia', () => {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify({ privacyMode: true, offlineCacheEnabled: false }))

    setActivePinia(createPinia())
    const ui = useUiStore()
    expect(ui.privacyMode).toBe(true)
    expect(ui.offlineCacheEnabled).toBe(false)
  })

  it('rehydrates serverBaseUrl from the dedicated key on a fresh Pinia', () => {
    localStorage.setItem(SERVER_URL_KEY, 'https://tunnel.example.com')
    localStorage.setItem(SETTINGS_KEY, JSON.stringify({ offlineCacheEnabled: true }))

    setActivePinia(createPinia())
    const ui = useUiStore()
    expect(ui.serverBaseUrl).toBe('https://tunnel.example.com')
  })

  it('persists multiple state changes via the same watch with one localStorage write', async () => {
    const ui = useUiStore()
    ui.serverBaseUrl = 'https://x.com'
    ui.privacyMode = true
    ui.offlineCacheEnabled = false

    await new Promise(resolve => setTimeout(resolve, 0))

    expect(localStorage.getItem(SERVER_URL_KEY)).toBe('https://x.com')
    const stored = JSON.parse(localStorage.getItem(SETTINGS_KEY))
    expect(stored).toMatchObject({
      privacyMode: true,
      offlineCacheEnabled: false
    })
  })
})
