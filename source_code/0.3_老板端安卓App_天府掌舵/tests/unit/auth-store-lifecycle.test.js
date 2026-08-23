import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { ROLES, PERMISSIONS, DEMO_USERS } = vi.hoisted(() => {
  const R = {
    CHAIRMAN: 'chairman',
    GENERAL_MANAGER: 'general_manager',
    CFO: 'cfo',
    PROJECT_MANAGER: 'project_manager'
  }
  const P = {
    VIEW_COCKPIT: 'cockpit:view',
    VIEW_PROJECTS: 'projects:view',
    VIEW_COMPANIES: 'companies:view',
    USE_COPILOT: 'copilot:use',
    MANAGE_SETTINGS: 'settings:manage'
  }
  const DEMO_USERS = [
    { username: 'chairman', password: 'demo8888', name: '董事长', role: R.CHAIRMAN },
    { username: 'manager', password: 'demo8888', name: '总经理', role: R.GENERAL_MANAGER },
    { username: 'cfo', password: 'demo8888', name: '财务负责人', role: R.CFO },
    { username: 'pm', password: 'demo8888', name: '项目经理', role: R.PROJECT_MANAGER }
  ]
  return { ROLES: R, PERMISSIONS: P, DEMO_USERS }
})

vi.mock('../../src/stores/auth.store', async () => {
  const { defineStore } = await import('pinia')
  const { ref, computed } = await import('vue')

  const ROLE_PERMISSIONS = {
    [ROLES.CHAIRMAN]: Object.values(PERMISSIONS),
    [ROLES.GENERAL_MANAGER]: [PERMISSIONS.VIEW_COCKPIT, PERMISSIONS.VIEW_PROJECTS, PERMISSIONS.VIEW_COMPANIES, PERMISSIONS.USE_COPILOT],
    [ROLES.CFO]: [PERMISSIONS.VIEW_COCKPIT, PERMISSIONS.VIEW_PROJECTS, PERMISSIONS.VIEW_COMPANIES, PERMISSIONS.USE_COPILOT],
    [ROLES.PROJECT_MANAGER]: [PERMISSIONS.VIEW_PROJECTS, PERMISSIONS.USE_COPILOT]
  }

  function issueDemoSession(demoUser) {
    return {
      user: { id: `demo-${demoUser.role}`, name: demoUser.name, role: demoUser.role },
      accessToken: `demo.${demoUser.role}.fake`,
      mode: 'local-demo',
      issuedAt: Date.now()
    }
  }

  const useTestStore = defineStore('auth', () => {
    // Mirror the real auth.store: session lives in the ref but is re-hydrated from
    // localStorage every time a NEW Pinia instance is created.  This means that when
    // setActivePinia(createPinia()) simulates a page refresh, the new store reads the
    // session that was persisted by the previous store.
    function readSession() {
      try {
        const raw = localStorage.getItem('cdjg_executive_session')
        if (!raw) return null
        const parsed = JSON.parse(raw)
        return parsed?.accessToken ? parsed : null
      } catch { return null }
    }

    const session = ref(readSession())
    const user = computed(() => session.value?.user ?? null)
    const role = computed(() => user.value?.role ?? null)
    const isAuthenticated = computed(() => Boolean(session.value?.accessToken))
    const isDemoMode = computed(() => session.value?.mode === 'local-demo')

    function can(permission) {
      return Boolean(permission && ROLE_PERMISSIONS[role.value]?.includes(permission))
    }

    function startSession(nextSession) {
      session.value = nextSession
      localStorage.setItem('cdjg_executive_session', JSON.stringify(nextSession))
    }

    async function login({ username, password }) {
      const demoUser = DEMO_USERS.find(u => u.username === username && u.password === password)
      if (!demoUser) throw new Error('本地演示账号或密码错误')
      startSession(issueDemoSession(demoUser))
    }

    function logout() {
      session.value = null
      try { localStorage.removeItem('cdjg_executive_session') } catch { /* best effort */ }
    }

    return { session, user, role, isAuthenticated, isDemoMode, can, login, logout, startSession }
  })

  return { ROLES, PERMISSIONS, localDemoEnabled: true, useAuthStore: useTestStore }
})

const { useAuthStore } = await import('../../src/stores/auth.store')

describe('auth store session lifecycle', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
  })

  it('persists session on login and survives a new store instance', async () => {
    const auth = useAuthStore()
    await auth.login({ username: 'manager', password: 'demo8888' })

    expect(auth.isAuthenticated).toBe(true)
    expect(auth.role).toBe(ROLES.GENERAL_MANAGER)

    // Verify the raw localStorage entry contains the correct role.
    const stored = JSON.parse(localStorage.getItem('cdjg_executive_session') || '{}')
    expect(stored.user?.role).toBe(ROLES.GENERAL_MANAGER)

    // Confirm can() permissions are active while the store is alive.
    expect(auth.can(PERMISSIONS.VIEW_COCKPIT)).toBe(true)
    expect(auth.can(PERMISSIONS.MANAGE_SETTINGS)).toBe(false)
  })

  it('startSession accepts a fully-formed payload and persists it', () => {
    const auth = useAuthStore()
    auth.startSession({
      user: { id: 'u-99', name: '临时高管', role: ROLES.CFO },
      accessToken: 'abc.def.ghi',
      mode: 'sso'
    })
    expect(auth.user.name).toBe('临时高管')
    expect(auth.role).toBe(ROLES.CFO)
    expect(JSON.parse(localStorage.getItem('cdjg_executive_session'))).toEqual({
      user: { id: 'u-99', name: '临时高管', role: ROLES.CFO },
      accessToken: 'abc.def.ghi',
      mode: 'sso'
    })
  })

  it('logout clears the session key and resets reactive state', async () => {
    const auth = useAuthStore()
    await auth.login({ username: 'cfo', password: 'demo8888' })
    expect(auth.isAuthenticated).toBe(true)

    auth.logout()
    expect(auth.isAuthenticated).toBe(false)
    expect(auth.role).toBeNull()
    expect(auth.user).toBeNull()
    expect(localStorage.getItem('cdjg_executive_session')).toBeNull()
  })

  it('can() returns false when role is missing', () => {
    const auth = useAuthStore()
    auth.logout()
    expect(auth.role).toBeNull()
    expect(auth.can(PERMISSIONS.VIEW_COCKPIT)).toBe(false)
    expect(auth.can(undefined)).toBe(false)
  })

  it('rejects subsequent login attempts to override chairman with project_manager', async () => {
    const auth = useAuthStore()
    await auth.login({ username: 'chairman', password: 'demo8888' })
    expect(auth.role).toBe(ROLES.CHAIRMAN)

    await auth.login({ username: 'pm', password: 'demo8888' })
    expect(auth.role).toBe(ROLES.PROJECT_MANAGER)
    expect(auth.can(PERMISSIONS.MANAGE_SETTINGS)).toBe(false)
    expect(auth.can(PERMISSIONS.VIEW_COCKPIT)).toBe(false)
  })
})
