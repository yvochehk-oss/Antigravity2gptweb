import { createRouter, createWebHashHistory } from 'vue-router'
import AppShell from '../app/AppShell.vue'
import LoginView from '../modules/auth/LoginView.vue'
import { PERMISSIONS, useAuthStore } from '../stores/auth.store'

const routes = [
  { path: '/login', name: 'login', component: LoginView },
  {
    path: '/',
    component: AppShell,
    meta: { requiresAuth: true },
    children: [
      { path: '', redirect: { name: 'dashboard' } },
      { path: 'dashboard', name: 'dashboard', component: () => import('../modules/dashboard/DashboardView.vue'), meta: { permission: PERMISSIONS.VIEW_COCKPIT } },
      { path: 'projects', name: 'projects', component: () => import('../modules/projects/ProjectsView.vue'), meta: { permission: PERMISSIONS.VIEW_PROJECTS } },
      { path: 'companies', name: 'companies', component: () => import('../modules/companies/CompaniesView.vue'), meta: { permission: PERMISSIONS.VIEW_COMPANIES } },
      { path: 'copilot', name: 'copilot', component: () => import('../modules/ai/CopilotView.vue'), meta: { permission: PERMISSIONS.USE_COPILOT } },
      { path: 'settings', name: 'settings', component: () => import('../modules/settings/SettingsView.vue'), meta: { permission: PERMISSIONS.MANAGE_SETTINGS } }
    ]
  },
  { path: '/:pathMatch(.*)*', redirect: { name: 'dashboard' } }
]

const router = createRouter({
  history: createWebHashHistory(),
  routes,
  scrollBehavior: () => ({ top: 0 })
})

router.beforeEach(to => {
  const auth = useAuthStore()
  if (to.meta.requiresAuth && !auth.isAuthenticated) return { name: 'login', query: { redirect: to.fullPath } }
  if (to.name === 'login' && auth.isAuthenticated) {
    if (auth.can(PERMISSIONS.VIEW_COCKPIT)) return { name: 'dashboard' }
    if (auth.can(PERMISSIONS.VIEW_PROJECTS)) return { name: 'projects' }
    if (auth.can(PERMISSIONS.VIEW_COMPANIES)) return { name: 'companies' }
    if (auth.can(PERMISSIONS.USE_COPILOT)) return { name: 'copilot' }
    if (auth.can(PERMISSIONS.MANAGE_SETTINGS)) return { name: 'settings' }
    return true
  }
  if (to.meta.permission && !auth.can(to.meta.permission)) {
    if (auth.can(PERMISSIONS.VIEW_COCKPIT)) return { name: 'dashboard' }
    if (auth.can(PERMISSIONS.VIEW_PROJECTS)) return { name: 'projects' }
    if (auth.can(PERMISSIONS.VIEW_COMPANIES)) return { name: 'companies' }
    if (auth.can(PERMISSIONS.USE_COPILOT)) return { name: 'copilot' }
    if (auth.can(PERMISSIONS.MANAGE_SETTINGS)) return { name: 'settings' }
    auth.logout()
    return { name: 'login' }
  }
  return true
})

export default router
