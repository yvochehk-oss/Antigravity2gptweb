import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { clearSnapshots } from '../offline/snapshot'

export const ROLES = Object.freeze({
  ADMIN: 'admin',
  EXECUTIVE: 'executive',
  OPERATOR: 'operator'
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
  [ROLES.EXECUTIVE]: [PERMISSIONS.VIEW_COCKPIT, PERMISSIONS.VIEW_PROJECTS, PERMISSIONS.VIEW_COMPANIES, PERMISSIONS.USE_COPILOT, PERMISSIONS.MANAGE_SETTINGS]
}

/**
 * Local demo mode is OPT-IN, not default. Production builds must NOT enable
 * it unless ``VITE_ENABLE_LOCAL_DEMO=true`` is explicitly set during the
 * build. This closes the auth bypass that shipped when the env var was
 * missing (it defaulted to truthy).
 */
export const localDemoEnabled = import.meta.env.VITE_ENABLE_LOCAL_DEMO === 'true'

/**
 * Tax backend base URL.  Defaults to the RAG API URL so that a single
 * ``VITE_API_BASE_URL`` value works for both.  Set ``VITE_TAX_API_BASE_URL``
 * to deploy Tax and RAG on different hosts.
 */
export const TAX_API_BASE_URL =
  import.meta.env.VITE_TAX_API_BASE_URL?.trim() ||
  import.meta.env.VITE_API_BASE_URL?.trim() ||
  'http://127.0.0.1:8922'

const SESSION_KEY = 'cdjg_executive_session'

const DEMO_USERS = [
  { id: 'demo-admin', username: 'admin', password: '888888', name: '系统管理员', role: ROLES.ADMIN },
  { id: 'demo-operator', username: 'operator', password: '888888', name: '业务操作员', role: ROLES.OPERATOR }
]

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

function issueDemoSession(demoUser) {
  // Demo tokens carry a fixed prefix so the backend can refuse them when
  // demo mode is off, even if someone tampers with the bundle.
  const token = `demo.${demoUser.role}.${cryptoLikeRandom()}`
  return {
    user: { id: demoUser.id, name: demoUser.name, role: demoUser.role },
    accessToken: token,
    mode: 'local-demo',
    issuedAt: Date.now()
  }
}

function cryptoLikeRandom() {
  if (typeof crypto !== 'undefined' && crypto.getRandomValues) {
    const bytes = crypto.getRandomValues(new Uint8Array(16))
    return Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('')
  }
  return Math.random().toString(36).slice(2, 18)
}

export const useAuthStore = defineStore('auth', () => {
  const session = ref(readStoredSession())
  const user = computed(() => session.value?.user ?? null)
  const role = computed(() => user.value?.role ?? null)
  const isAuthenticated = computed(() => Boolean(session.value?.accessToken))
  const isDemoMode = computed(() => session.value?.mode === 'local-demo')

  function can(permission) {
    return Boolean(permission && ROLE_PERMISSIONS[role.value]?.includes(permission))
  }

  function startSession(nextSession) {
    session.value = nextSession
    localStorage.setItem(SESSION_KEY, JSON.stringify(nextSession))
  }

  async function login({ username: inputUsername, password: inputPassword }) {
    // --- Production path: Tax backend issues a real JWT ---
    if (inputUsername && inputPassword) {
      try {
        const response = await fetch(`${TAX_API_BASE_URL}/api/v1/auth/token`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
          body: new URLSearchParams({
            username: inputUsername,
            password: inputPassword,
          })
        })
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
        if (response.status === 401) {
          if (!localDemoEnabled) {
            throw new Error('用户名或密码错误')
          }
          // Fall through to demo path
        } else {
          const errorData = await response.json().catch(() => ({}))
          throw new Error(errorData.detail || `服务器错误 (${response.status})`)
        }
      } catch (error) {
        if (!localDemoEnabled) throw error
        // Demo mode: show demo credentials UI only
      }
    }

    // --- Demo path: only when VITE_ENABLE_LOCAL_DEMO=true ---
    if (!localDemoEnabled) {
      throw new Error('请输入正式账号登录，或在构建时设置 VITE_ENABLE_LOCAL_DEMO=true 开启演示模式。')
    }
    const demoUser = DEMO_USERS.find(
      u => u.username === inputUsername && u.password === inputPassword
    )
    if (!demoUser) {
      throw new Error('本地演示账号或密码错误，请查阅 README 中的 demo 凭据。')
    }
    startSession(issueDemoSession(demoUser))
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