import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { clearSnapshots } from '../offline/snapshot'

export const ROLES = Object.freeze({
  ADMIN: 'admin',
  EXECUTIVE: 'executive',
  OPERATOR: 'operator',
  // Backend SSO historically used these executive role names. Keep them as
  // first-class roles so a rehydrated/SSO session does not get logged out
  // merely because the mobile client uses the newer short role vocabulary.
  CHAIRMAN: 'chairman',
  GENERAL_MANAGER: 'general_manager',
  CFO: 'cfo',
  PROJECT_MANAGER: 'project_manager'
})

export const PERMISSIONS = Object.freeze({
  MANAGE_USERS: 'users:manage',
  VIEW_ALL_DATA: 'data:view_all',
  MANAGE_SETTINGS: 'settings:manage',
  VIEW_COCKPIT: 'view:cockpit',
  VIEW_PROJECTS: 'view:projects',
  VIEW_COMPANIES: 'view:companies',
  USE_COPILOT: 'use:copilot'
})

const ROLE_PERMISSIONS = {
  [ROLES.ADMIN]: Object.values(PERMISSIONS),
  [ROLES.OPERATOR]: [PERMISSIONS.VIEW_ALL_DATA],
  [ROLES.EXECUTIVE]: [PERMISSIONS.VIEW_COCKPIT, PERMISSIONS.VIEW_PROJECTS, PERMISSIONS.VIEW_COMPANIES, PERMISSIONS.USE_COPILOT, PERMISSIONS.MANAGE_SETTINGS],
  [ROLES.CHAIRMAN]: Object.values(PERMISSIONS),
  [ROLES.GENERAL_MANAGER]: [PERMISSIONS.VIEW_COCKPIT, PERMISSIONS.VIEW_PROJECTS, PERMISSIONS.VIEW_COMPANIES, PERMISSIONS.USE_COPILOT],
  [ROLES.CFO]: [PERMISSIONS.VIEW_COCKPIT, PERMISSIONS.VIEW_PROJECTS, PERMISSIONS.VIEW_COMPANIES, PERMISSIONS.USE_COPILOT],
  [ROLES.PROJECT_MANAGER]: [PERMISSIONS.VIEW_PROJECTS, PERMISSIONS.USE_COPILOT]
}

/**
 * Local demo mode is development-only and opt-in. Vite's production build
 * replaces ``import.meta.env.DEV`` with ``false``; keeping that guard in the
 * expression also lets Rollup remove the demo credential branch from the
 * production bundle even if a developer accidentally leaves the opt-in flag
 * in a local .env file.
 */
export const localDemoEnabled =
  import.meta.env.DEV && import.meta.env.VITE_ENABLE_LOCAL_DEMO === 'true'

/**
 * Tax backend base URL.  Defaults to the RAG API URL so that a single
 * ``VITE_API_BASE_URL`` value works for both.  Set ``VITE_TAX_API_BASE_URL``
 * to deploy Tax and RAG on different hosts.
 */
export const TAX_API_BASE_URL =
  import.meta.env.VITE_TAX_API_BASE_URL?.trim() ||
  'http://127.0.0.1:8921'

const SESSION_KEY = 'cdjg_executive_session'

function readStoredSession() {
  try {
    const raw = localStorage.getItem(SESSION_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw)
    if (parsed && parsed.accessToken) return parsed
    return null
  } catch {
    return null
  }
}

function localDemoModeName() {
  return ['local', 'demo'].join('-')
}

function findLocalDemoSession(username, password) {
  // Keep demo-only credentials behind both compile-time/runtime guards. The
  // production Vite build can then eliminate this branch and its literals.
  if (!localDemoEnabled) return null
  const demoUsers = [
    { id: 1, username: 'admin', password: '888888', name: '系统管理员', role: ROLES.ADMIN },
    { id: 2, username: 'operator', password: '888888', name: '业务操作员', role: ROLES.OPERATOR }
  ]
  const demoUser = demoUsers.find(user => user.username === username && user.password === password)
  if (!demoUser) return null
  const random = typeof crypto !== 'undefined' && crypto.getRandomValues
    ? Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, '0')).join('')
    : Math.random().toString(36).slice(2, 18)
  return {
    user: { id: demoUser.id, name: demoUser.name, role: demoUser.role },
    accessToken: `demo.${demoUser.role}.${random}`,
    mode: localDemoModeName(),
    issuedAt: Date.now()
  }
}

function isLocalDemoSession(currentSession) {
  return Boolean(localDemoEnabled && currentSession?.mode === localDemoModeName())
}

export const useAuthStore = defineStore('auth', () => {
  const session = ref(readStoredSession())
  const user = computed(() => session.value?.user ?? null)
  const role = computed(() => user.value?.role ?? null)
  const isAuthenticated = computed(() => Boolean(session.value?.accessToken))
  const isDemoMode = computed(() => isLocalDemoSession(session.value))

  function can(permission) {
    return Boolean(permission && ROLE_PERMISSIONS[role.value]?.includes(permission))
  }

  function startSession(nextSession) {
    session.value = nextSession
    localStorage.setItem(SESSION_KEY, JSON.stringify(nextSession))
  }

  async function login({ username: inputUsername, password: inputPassword }) {
    if (!inputUsername || !inputPassword) throw new Error('请输入账号和密码')

    let response
    try {
      response = await fetch(`${TAX_API_BASE_URL}/api/v1/auth/token`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams({
          username: inputUsername,
          password: inputPassword,
        })
      })
    } catch (error) {
      // A development-only opt-in may use the local demo account while the
      // backend is intentionally absent. Production must always fail closed.
      const demoSession = findLocalDemoSession(inputUsername, inputPassword)
      if (demoSession) {
        startSession(demoSession)
        return
      }
      console.warn('Backend login request failed:', error)
      throw new Error('认证服务暂时不可用，请确认后台服务已启动后重试')
    }

    if (response.ok) {
      const data = await response.json()
      startSession({
        user: data.user,
        accessToken: data.access_token,
        refreshToken: data.refresh_token,
        expiresIn: data.expires_in,
        mode: 'production',
        issuedAt: Date.now()
      })
      return
    }

    // A 401 can be used by an explicitly opted-in development build to enter
    // its local demo account. No other backend error is silently downgraded.
    if (response.status === 401) {
      const demoSession = findLocalDemoSession(inputUsername, inputPassword)
      if (demoSession) {
        startSession(demoSession)
        return
      }
    }

    const errorData = await response.json().catch(() => ({}))
    throw new Error(errorData.detail || `登录失败 (${response.status})`)
  }

  /**
   * Exchange the stored refresh_token for a new access + refresh pair.
   * Called automatically when an API call returns 401.
   */
  async function refreshSession() {
    const refreshToken = session.value?.refreshToken
    if (!refreshToken) return false
    try {
      const response = await fetch(`${TAX_API_BASE_URL}/api/v1/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: refreshToken })
      })
      if (!response.ok) return false
      const data = await response.json()
      startSession({
        ...session.value,
        accessToken: data.access_token,
        refreshToken: data.refresh_token,
        expiresIn: data.expires_in,
        issuedAt: Date.now()
      })
      return true
    } catch {
      return false
    }
  }

  function logout() {
    session.value = null
    try {
      localStorage.removeItem(SESSION_KEY)
    } catch {
      // best effort
    }
    clearSnapshots()
  }

  return {
    session,
    user,
    role,
    isAuthenticated,
    isDemoMode,
    can,
    login,
    logout,
    refreshSession,
    startSession
  }
})
