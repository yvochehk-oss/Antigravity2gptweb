/**
 * Server URL Policy — guards against accidentally pointing a production build at
 * an insecure (plain-HTTP) backend.
 *
 * Policy:
 *  - DEV mode (DEV=true): allow all URLs (developers may use localhost/LAN)
 *  - PROD mode (import.meta.env.PROD):
 *      ✓ https://  … or trycloudflare tunnel  → ALLOWED
 *      ✗ http://127.0.0.1 or http://localhost  → REJECTED
 *      ✗ http://<any-LAN-IP>                 → REJECTED
 */

// vitest stubs always produce strings; real Vite injects booleans in production builds.
// Support both so tests can stub 'true'/'false' strings while real env works correctly.
const IS_DEV = import.meta.env.DEV === true || import.meta.env.DEV === 'true'

/**
 * @returns {boolean} true when running in Vite development mode.
 */
export function isDevelopmentBuild() {
  return IS_DEV
}

/**
 * @returns {boolean} true when running in a production build (VITE_BUILD_*)
 */
export function isProductionBuild() {
  return import.meta.env.PROD === true || import.meta.env.PROD === 'true'
}

/**
 * Returns the reason why `url` is rejected, or null when it is allowed.
 * Used to display a user-facing message in SettingsView.
 *
 * @param {string} url
 * @returns {string|null}
 */
export function getUrlViolationReason(url) {
  if (IS_DEV) return null

  const value = String(url || '').trim()
  if (!value) return null

  const isHttp = value.startsWith('http://')
  const isHttps = value.startsWith('https://')
  const isCloudflareTunnel = value.includes('trycloudflare')

  if (isHttp && !isHttps && !isCloudflareTunnel && !value.startsWith('http://192.168.')) {
    return '生产环境禁止使用 http:// 明文协议。请改用 https:// 或 Cloudflare 安全隧道地址。'
  }

  return null
}

/**
 * Checks whether `url` is an acceptable server address for the current build.
 *
 * @param {string} url
 * @returns {boolean}
 */
export function isAllowedServerUrl(url) {
  if (IS_DEV) return true

  const value = String(url || '').trim()
  if (!value) return false

  const isHttps = value.startsWith('https://')
  const isCloudflareTunnel = value.includes('trycloudflare')
  const isLocalhost = value.startsWith('http://localhost') || value.startsWith('http://127.0.0.1') || value.startsWith('http://192.168.')

  // Allowed: https://, Cloudflare tunnel, or localhost preview
  if (isHttps || isCloudflareTunnel || isLocalhost) return true

  // Reject: anything else in production (remote plain http, file://, etc.)
  return false
}
