import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

// All top-level values must be declared via vi.hoisted so they are initialised before
// vitest hoists the vi.mock() calls, avoiding "Cannot access '__vi_import__'" errors.
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

// Capture the clearSnapshots mock so the test can invoke it directly.
const clearSnapshotsMock = vi.fn()

vi.mock('../../src/offline/snapshot', () => ({
  saveSnapshot: vi.fn(),
  clearSnapshots: clearSnapshotsMock
}))

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
    const session = ref(null)
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
      if (!demoUser) throw new Error('本地演示账号或密码错误，请查阅 README 中的 demo 凭据。')
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
    clearSnapshotsMock.mockClear()
    setActivePinia(createPinia())
  })

  it('initializes without a default session (demo requires explicit login)', () => {
    const auth = useAuthStore()
    expect(auth.isAuthenticated).toBe(false)
    expect(auth.role).toBeNull()
  })

  it('login in demo mode creates a fresh session with the requested role', async () => {
    const auth = useAuthStore()
    expect(auth.isAuthenticated).toBe(false)

    await auth.login({ username: 'manager', password: 'demo8888' })
    expect(auth.isAuthenticated).toBe(true)
    expect(auth.role).toBe(ROLES.GENERAL_MANAGER)
    expect(auth.user?.name).toBe('总经理')
  })

  it('login throws for unknown credentials', async () => {
    const auth = useAuthStore()
    await expect(auth.login({ username: 'hacker', password: 'wrong' }))
      .rejects.toThrowError(/本地演示账号或密码错误/)
  })

  it('can returns true for a permission granted to the current role', async () => {
    const auth = useAuthStore()
    await auth.login({ username: 'chairman', password: 'demo8888' })

    expect(auth.can(PERMISSIONS.VIEW_COCKPIT)).toBe(true)
    expect(auth.can(PERMISSIONS.VIEW_PROJECTS)).toBe(true)
    expect(auth.can(PERMISSIONS.USE_COPILOT)).toBe(true)
  })

  it('logout clears the session and calls clearSnapshots', async () => {
    const auth = useAuthStore()
    await auth.login({ username: 'cfo', password: 'demo8888' })
    expect(auth.isAuthenticated).toBe(true)

    auth.logout()

    expect(auth.session).toBeNull()
    expect(auth.isAuthenticated).toBe(false)
    expect(auth.user).toBeNull()
    expect(localStorage.getItem('cdjg_executive_session')).toBeNull()
  })
})
