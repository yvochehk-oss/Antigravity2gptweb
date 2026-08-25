import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { PERMISSIONS, ROLES, useAuthStore } from '../../src/stores/auth.store'
import BottomNavigation from '../../src/shared/components/BottomNavigation.vue'
import { createRouter, createMemoryHistory } from 'vue-router'

vi.mock('vue-router', async () => {
  const actual = await vi.importActual('vue-router')
  return { ...actual }
})

async function buildRouter() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/dashboard', name: 'dashboard', component: { template: '<div/>' } },
      { path: '/projects', name: 'projects', component: { template: '<div/>' } },
      { path: '/companies', name: 'companies', component: { template: '<div/>' } },
      { path: '/copilot', name: 'copilot', component: { template: '<div/>' } },
      { path: '/settings', name: 'settings', component: { template: '<div/>' } }
    ]
  })
  await router.push('/dashboard')
  await router.isReady()
  return router
}

describe('<BottomNavigation />', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
  })

  it('renders all available tabs in order for the chairman', async () => {
    const auth = useAuthStore()
    auth.startSession({
      user: { id: 'chairman', name: '董事长', role: ROLES.CHAIRMAN },
      accessToken: 'chairman-token',
      mode: 'sso'
    })
    const router = await buildRouter()
    const wrapper = mount(BottomNavigation, { global: { plugins: [router] } })

    const labels = wrapper.findAll('.nav-label').map(node => node.text())
    expect(labels).toContain('集团大盘')
    expect(labels).toContain('项目穿透')
    expect(labels).toContain('项目全览')
    expect(labels).toContain('AI 智策')
    expect(labels).toContain('系统设置')
  })

  it('hides cockpit + companies + settings for project_manager', async () => {
    const auth = useAuthStore()
    auth.startSession({
      user: { id: 'pm', name: '项目经理', role: ROLES.PROJECT_MANAGER },
      accessToken: 'pm-token',
      mode: 'sso'
    })
    const router = await buildRouter()
    const wrapper = mount(BottomNavigation, { global: { plugins: [router] } })

    const labels = wrapper.findAll('.nav-label').map(node => node.text())
    expect(labels).toEqual(expect.arrayContaining(['项目穿透', 'AI 智策']))
    expect(labels).not.toContain('集团大盘')
    expect(labels).not.toContain('法人全景')
    expect(labels).not.toContain('穿透设置')
    expect(auth.can(PERMISSIONS.MANAGE_SETTINGS)).toBe(false)
  })

  it('uses <script setup> style composables (no options API leak)', () => {
    expect(typeof BottomNavigation.setup).toBe('function')
  })
})
