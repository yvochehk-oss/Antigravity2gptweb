import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../src/api/dashboard.api', () => ({ getCockpitSummary: vi.fn() }))
vi.mock('../../src/api/companies.api', () => ({ getCompanyMatrix: vi.fn() }))
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
const { ApiError } = await import('../../src/api/client')
const { getProject360 } = await import('../../src/api/projects.api')

describe('executive store openProject RBAC', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
    vi.mocked(getProject360).mockReset()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('throws when the current role lacks VIEW_PROJECTS', async () => {
    const auth = useAuthStore()
    auth.logout()
    expect(auth.can(PERMISSIONS.VIEW_PROJECTS)).toBe(false)

    const executive = useExecutiveStore()
    await expect(executive.openProject('proj-1')).rejects.toThrow(/当前角色无权访问项目详情/)
    expect(getProject360).not.toHaveBeenCalled()
  })

  it('allows a project_manager (which has VIEW_PROJECTS) to call openProject', async () => {
    const auth = useAuthStore()
    await auth.login({ username: 'pm', password: 'demo8888' })

    expect(auth.can(PERMISSIONS.VIEW_PROJECTS)).toBe(true)

    vi.mocked(getProject360).mockResolvedValue({ id: 'proj-99', name: '天府二期' })

    const executive = useExecutiveStore()
    await executive.openProject('proj-99')

    expect(getProject360).toHaveBeenCalledTimes(1)
    expect(getProject360).toHaveBeenCalledWith(
      expect.any(String),
      'proj-99',
      expect.stringMatching(/^demo\.project_manager\./)
    )
    expect(executive.activeProject360).toEqual({ id: 'proj-99', name: '天府二期' })
  })

  it('encodes special characters in projectId when calling the API', async () => {
    const auth = useAuthStore()
    await auth.login({ username: 'chairman', password: 'demo8888' })

    vi.mocked(getProject360).mockImplementation(async (_baseUrl, projectId) => {
      return { id: projectId, name: '天府二期' }
    })

    const executive = useExecutiveStore()
    await executive.openProject('proj/special 1')

    expect(getProject360).toHaveBeenCalledWith(
      expect.any(String),
      'proj/special 1',
      expect.stringMatching(/^demo\.chairman\./)
    )
    expect(executive.activeProject360.id).toBe('proj/special 1')
  })

  it('stores the returned payload on activeProject360', async () => {
    const auth = useAuthStore()
    await auth.login({ username: 'chairman', password: 'demo8888' })

    const payload = {
      id: 'p1',
      project_code: 'P-001',
      kpi: { revenue_recognized: 12_800_000 }
    }
    vi.mocked(getProject360).mockResolvedValue(payload)

    const executive = useExecutiveStore()
    await executive.openProject('p1')

    expect(executive.activeProject360).toEqual(payload)
  })

  it('propagates network errors as ApiError without swallowing', async () => {
    const auth = useAuthStore()
    await auth.login({ username: 'chairman', password: 'demo8888' })

    vi.mocked(getProject360).mockRejectedValue(new ApiError('API 请求失败 (500)', { status: 500 }))

    const executive = useExecutiveStore()
    await expect(executive.openProject('p1')).rejects.toBeInstanceOf(ApiError)
    await expect(executive.openProject('p1')).rejects.toMatchObject({ status: 500 })
    expect(executive.activeProject360).toBeNull()
  })
})
