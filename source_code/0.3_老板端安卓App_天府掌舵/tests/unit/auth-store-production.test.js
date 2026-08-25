import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

async function loadAuthStore(enableLocalDemo = false) {
  vi.resetModules()
  vi.stubEnv('VITE_ENABLE_LOCAL_DEMO', enableLocalDemo ? 'true' : '')
  return import('../../src/stores/auth.store')
}

describe('production authentication boundary', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllEnvs()
  })

  it('fails closed when the backend is unavailable and local demo is not opted in', async () => {
    const { localDemoEnabled, useAuthStore } = await loadAuthStore()
    expect(localDemoEnabled).toBe(false)

    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('backend offline')))
    const auth = useAuthStore()

    await expect(auth.login({ username: 'admin', password: '888888' }))
      .rejects.toThrow(/认证服务暂时不可用/)
    expect(auth.isAuthenticated).toBe(false)
    expect(auth.session).toBeNull()
    expect(localStorage.getItem('cdjg_executive_session')).toBeNull()
  })

  it('does not downgrade a rejected backend login into a virtual session', async () => {
    const { localDemoEnabled, useAuthStore } = await loadAuthStore()
    expect(localDemoEnabled).toBe(false)

    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: false,
      status: 401,
      json: vi.fn().mockResolvedValue({ detail: '账号或密码错误' })
    }))
    const auth = useAuthStore()

    await expect(auth.login({ username: 'operator', password: '888888' }))
      .rejects.toThrow('账号或密码错误')
    expect(auth.isAuthenticated).toBe(false)
    expect(localStorage.getItem('cdjg_executive_session')).toBeNull()
  })

  it('creates a production session only from a successful backend response', async () => {
    const { localDemoEnabled, useAuthStore } = await loadAuthStore()
    expect(localDemoEnabled).toBe(false)

    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: vi.fn().mockResolvedValue({
        user: { id: 'u-1', name: '正式用户', role: 'executive' },
        access_token: 'access.real',
        refresh_token: 'refresh.real',
        expires_in: 3600
      })
    }))
    const auth = useAuthStore()

    await auth.login({ username: 'real-user', password: 'real-password' })

    expect(auth.isAuthenticated).toBe(true)
    expect(auth.isDemoMode).toBe(false)
    expect(auth.session).toMatchObject({
      user: { id: 'u-1', role: 'executive' },
      accessToken: 'access.real',
      refreshToken: 'refresh.real',
      mode: 'production'
    })
  })

  it('allows local demo only in Vite development with explicit opt-in', async () => {
    const { localDemoEnabled, useAuthStore } = await loadAuthStore(true)
    expect(import.meta.env.DEV).toBe(true)
    expect(localDemoEnabled).toBe(true)

    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('development backend offline')))
    const auth = useAuthStore()

    await auth.login({ username: 'admin', password: '888888' })

    expect(auth.isAuthenticated).toBe(true)
    expect(auth.isDemoMode).toBe(true)
    expect(auth.session).toMatchObject({
      user: { id: 1, role: 'admin' },
      mode: 'local-demo'
    })
  })
})
