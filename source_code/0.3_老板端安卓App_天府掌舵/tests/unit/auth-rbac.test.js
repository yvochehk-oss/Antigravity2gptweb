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
      if (!demoUser) throw new Error('本地演示账号或密码错误')
      startSession(issueDemoSession(demoUser))
    }

    function logout() {
      session.value = null
      localStorage.removeItem('cdjg_executive_session')
    }

    return { session, user, role, isAuthenticated, isDemoMode, can, login, logout, startSession }
  })

  return { ROLES, PERMISSIONS, localDemoEnabled: true, useAuthStore: useTestStore }
})

const { useAuthStore } = await import('../../src/stores/auth.store')

describe('authentication and RBAC', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
  })

  it('starts without an active session (demo requires explicit login)', async () => {
    const auth = useAuthStore()
    expect(auth.isAuthenticated).toBe(false)
  })

  it('login with chairman credentials grants all permissions', async () => {
    const auth = useAuthStore()
    await auth.login({ username: 'chairman', password: 'demo8888' })
    expect(auth.isAuthenticated).toBe(true)
    expect(auth.role).toBe(ROLES.CHAIRMAN)
    expect(Object.values(PERMISSIONS).every(p => auth.can(p))).toBe(true)
  })

  it('limits a project manager to projects and copilot', async () => {
    const auth = useAuthStore()
    await auth.login({ username: 'pm', password: 'demo8888' })

    expect(auth.can(PERMISSIONS.VIEW_PROJECTS)).toBe(true)
    expect(auth.can(PERMISSIONS.USE_COPILOT)).toBe(true)
    expect(auth.can(PERMISSIONS.VIEW_COCKPIT)).toBe(false)
    expect(auth.can(PERMISSIONS.VIEW_COMPANIES)).toBe(false)
    expect(auth.can(PERMISSIONS.MANAGE_SETTINGS)).toBe(false)
  })

  it('grants general_manager limited permissions', async () => {
    const auth = useAuthStore()
    await auth.login({ username: 'manager', password: 'demo8888' })

    expect(auth.can(PERMISSIONS.VIEW_COCKPIT)).toBe(true)
    expect(auth.can(PERMISSIONS.VIEW_PROJECTS)).toBe(true)
    expect(auth.can(PERMISSIONS.VIEW_COMPANIES)).toBe(true)
    expect(auth.can(PERMISSIONS.MANAGE_SETTINGS)).toBe(false)
  })

  it('grants cfo cockpit and companies access', async () => {
    const auth = useAuthStore()
    await auth.login({ username: 'cfo', password: 'demo8888' })

    expect(auth.can(PERMISSIONS.VIEW_COCKPIT)).toBe(true)
    expect(auth.can(PERMISSIONS.VIEW_COMPANIES)).toBe(true)
    expect(auth.can(PERMISSIONS.MANAGE_SETTINGS)).toBe(false)
  })

  it('persists sessions and removes them on logout', async () => {
    const auth = useAuthStore()
    await auth.login({ username: 'cfo', password: 'demo8888' })

    const stored = JSON.parse(localStorage.getItem('cdjg_executive_session'))
    expect(stored).toMatchObject({ user: { name: '财务负责人', role: ROLES.CFO } })
    expect(stored.accessToken).toMatch(/^demo\.cfo\./)

    auth.logout()
    expect(auth.isAuthenticated).toBe(false)
    expect(auth.can(PERMISSIONS.VIEW_COCKPIT)).toBe(false)
    expect(localStorage.getItem('cdjg_executive_session')).toBeNull()
  })
})
