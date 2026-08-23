import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'
import { ROLES, useAuthStore } from '../../src/stores/auth.store'

vi.mock('../../src/app/AppShell.vue', () => ({ default: { name: 'AppShellStub' } }))
vi.mock('../../src/modules/auth/LoginView.vue', () => ({ default: { name: 'LoginViewStub' } }))
vi.mock('../../src/modules/dashboard/DashboardView.vue', () => ({ default: { name: 'DashboardViewStub' } }))
vi.mock('../../src/modules/projects/ProjectsView.vue', () => ({ default: { name: 'ProjectsViewStub' } }))
vi.mock('../../src/modules/companies/CompaniesView.vue', () => ({ default: { name: 'CompaniesViewStub' } }))
vi.mock('../../src/modules/ai/CopilotView.vue', () => ({ default: { name: 'CopilotViewStub' } }))
vi.mock('../../src/modules/settings/SettingsView.vue', () => ({ default: { name: 'SettingsViewStub' } }))

async function buildRouter() {
  const mod = await import('../../src/router/index.js')
  return mod.default
}

async function awaitRouterReady(router) {
  await router.push('/').catch(() => {})
  await router.isReady()
}

describe('router guards', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
  })

  it('redirects unauthenticated users to /login when accessing /dashboard', async () => {
    const auth = useAuthStore()
    auth.logout()
    const router = await buildRouter()
    await awaitRouterReady(router)

    await router.push({ name: 'dashboard' }).catch(() => {})
    const current = router.currentRoute.value
    expect(current.name).toBe('login')
    expect(current.query.redirect).toBe('/dashboard')
  })

  it('redirects authenticated users away from /login to /dashboard', async () => {
    const auth = useAuthStore()
    // Use startSession() to bypass login() — demo mode is not required.
    auth.startSession({
      user: { id: 'chairman', name: '董事长', role: ROLES.CHAIRMAN },
      accessToken: 'sso-token',
      mode: 'sso'
    })
    const router = await buildRouter()
    await awaitRouterReady(router)

    await router.push({ name: 'login' }).catch(() => {})
    expect(router.currentRoute.value.name).toBe('dashboard')
  })

  it('redirects users without permission on /settings to a route they can access', async () => {
    const auth = useAuthStore()
    auth.startSession({
      user: { id: 'pm', name: '项目经理', role: ROLES.PROJECT_MANAGER },
      accessToken: 'pm-token',
      mode: 'sso'
    })
    const router = await buildRouter()
    await awaitRouterReady(router)

    await router.push({ name: 'settings' }).catch(() => {})
    const current = router.currentRoute.value
    expect(current.name).not.toBe('settings')
    expect(current.name).toBe('projects')
  })

  it('allows a project_manager to reach /copilot', async () => {
    const auth = useAuthStore()
    auth.startSession({
      user: { id: 'pm', name: '项目经理', role: ROLES.PROJECT_MANAGER },
      accessToken: 'pm-token',
      mode: 'sso'
    })
    const router = await buildRouter()
    await awaitRouterReady(router)

    await router.push({ name: 'copilot' }).catch(() => {})
    expect(router.currentRoute.value.name).toBe('copilot')
  })

  it('redirects unknown routes to the root (which redirects to /dashboard)', async () => {
    const auth = useAuthStore()
    auth.startSession({
      user: { id: 'chairman', name: '董事长', role: ROLES.CHAIRMAN },
      accessToken: 'chairman-token',
      mode: 'sso'
    })
    const router = await buildRouter()
    await awaitRouterReady(router)

    await router.push('/totally-unknown-route').catch(() => {})
    const current = router.currentRoute.value
    expect(['dashboard', '/'].includes(current.name) || current.path === '/').toBe(true)
  })

  it('logs out and redirects to /login when user has zero permissions for a restricted route', async () => {
    const auth = useAuthStore()
    auth.startSession({
      user: { id: 'guest', name: '临时访客', role: 'unknown_role' },
      accessToken: 'token'
    })
    const router = await buildRouter()
    await awaitRouterReady(router)

    await router.push({ name: 'settings' }).catch(() => {})
    expect(router.currentRoute.value.name).toBe('login')
    expect(auth.isAuthenticated).toBe(false)
  })
})
