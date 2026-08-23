import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../src/api/dashboard.api', () => ({
  getCockpitSummary: vi.fn()
}))
vi.mock('../../src/api/companies.api', () => ({
  getCompanyMatrix: vi.fn()
}))
vi.mock('../../src/api/projects.api', () => ({
  getProjects: vi.fn(),
  getProject360: vi.fn()
}))

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
const { useExecutiveStore } = await import('../../src/stores/executive.store')
const { useUiStore } = await import('../../src/stores/ui.store')
const { getCockpitSummary } = await import('../../src/api/dashboard.api')
const { getCompanyMatrix } = await import('../../src/api/companies.api')
const { getProjects } = await import('../../src/api/projects.api')

describe('executive store refresh', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
    vi.mocked(getCockpitSummary).mockReset()
    vi.mocked(getCompanyMatrix).mockReset()
    vi.mocked(getProjects).mockReset()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('fills cockpit/projects/companies when all three endpoints succeed', async () => {
    const auth = useAuthStore()
    await auth.login({ username: 'chairman', password: 'demo8888' })

    const cockpit = { kpi: { revenue_recognized: 128_000_000 }, urgent_risks: ['a', 'b'] }
    const projects = [{ id: 1, project_code: 'P-001' }, { id: 2, project_code: 'P-002' }]
    const companies = { matrix: [{ id: 'C-1', name: '天府一建' }] }

    vi.mocked(getCockpitSummary).mockResolvedValue(cockpit)
    vi.mocked(getProjects).mockResolvedValue(projects)
    vi.mocked(getCompanyMatrix).mockResolvedValue(companies)

    const executive = useExecutiveStore()
    await executive.refresh()

    expect(executive.cockpit).toEqual(cockpit)
    expect(executive.projects).toEqual(projects)
    expect(executive.companies).toEqual(companies)
    expect(executive.partialFailure).toBe(false)
    expect(executive.urgentRiskCount).toBe(2)
    expect(executive.lastRefreshAt).not.toBeNull()
    expect(useUiStore().connectionStatus).toBe('connected')
  })

  it('marks partialFailure and keeps cached data when some endpoints fail', async () => {
    const auth = useAuthStore()
    await auth.login({ username: 'chairman', password: 'demo8888' })

    vi.mocked(getCockpitSummary).mockResolvedValue({ kpi: { revenue_recognized: 1 }, urgent_risks: [] })
    vi.mocked(getProjects).mockRejectedValue(new Error('upstream down'))
    vi.mocked(getCompanyMatrix).mockResolvedValue({ matrix: [] })

    const executive = useExecutiveStore()
    executive.projects = [{ id: 99, project_code: 'CACHED' }]

    await executive.refresh()

    expect(executive.partialFailure).toBe(true)
    expect(executive.cockpit.kpi.revenue_recognized).toBe(1)
    expect(executive.companies).toEqual({ matrix: [] })
    expect(useUiStore().connectionStatus).toBe('offline')
  })

  it('throws when every endpoint fails', async () => {
    const auth = useAuthStore()
    await auth.login({ username: 'chairman', password: 'demo8888' })

    vi.mocked(getCockpitSummary).mockRejectedValue(new Error('cockpit boom'))
    vi.mocked(getProjects).mockRejectedValue(new Error('projects boom'))
    vi.mocked(getCompanyMatrix).mockRejectedValue(new Error('companies boom'))

    const executive = useExecutiveStore()
    await expect(executive.refresh()).rejects.toThrow()
    expect(useUiStore().connectionStatus).toBe('offline')
  })

  it('only calls the projects endpoint for a project manager', async () => {
    const auth = useAuthStore()
    await auth.login({ username: 'pm', password: 'demo8888' })

    vi.mocked(getProjects).mockResolvedValue([{ id: 1, project_code: 'P-001' }])

    const executive = useExecutiveStore()
    await executive.refresh()

    expect(getProjects).toHaveBeenCalledTimes(1)
    expect(getCockpitSummary).not.toHaveBeenCalled()
    expect(getCompanyMatrix).not.toHaveBeenCalled()
    expect(executive.projects).toEqual([{ id: 1, project_code: 'P-001' }])
    expect(executive.cockpit).toEqual({})
    expect(executive.companies).toEqual({})
    expect(auth.can(PERMISSIONS.VIEW_COCKPIT)).toBe(false)
    expect(auth.can(PERMISSIONS.VIEW_PROJECTS)).toBe(true)
  })

  it('skips network calls when there is no active session', async () => {
    const auth = useAuthStore()
    auth.logout()
    expect(auth.isAuthenticated).toBe(false)

    const executive = useExecutiveStore()
    await executive.refresh()

    expect(getCockpitSummary).not.toHaveBeenCalled()
    expect(getProjects).not.toHaveBeenCalled()
    expect(getCompanyMatrix).not.toHaveBeenCalled()
    expect(executive.lastRefreshAt).toBeNull()
    expect(useUiStore().connectionStatus).toBe('connected')
  })
})
