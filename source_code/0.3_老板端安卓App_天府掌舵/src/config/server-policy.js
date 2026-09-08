/**
 * Server URL Policy — guards against accidentally pointing a production build at
 * an insecure (plain-HTTP) backend.
 *
 * Policy:
 *  - DEV mode (DEV=true): allow all URLs (developers may use localhost/LAN)
 *  - PROD mode (import.meta.env.PROD):
 *      ✓ https:// / trycloudflare tunnel      → ALLOWED
 *      ✓ http://127.0.0.1 / http://localhost → ALLOWED
 *      ✗ public plain-http endpoints          → REJECTED
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

function parseUrl(value) {
  try {
    return new URL(String(value || '').trim())
  } catch {
    return null
  }
}

function isLoopbackHostname(hostname) {
  const value = String(hostname || '').trim().toLowerCase().replace(/^\[|\]$/g, '')
  if (value === 'localhost' || value.endsWith('.localhost') || value === '::1') return true

  const octets = value.split('.')
  return octets.length === 4
    && octets.every(octet => /^\d+$/.test(octet) && Number(octet) >= 0 && Number(octet) <= 255)
    && Number(octets[0]) === 127
}

function isLoopbackHttpUrl(value) {
  const parsed = parseUrl(value)
  return Boolean(parsed && parsed.protocol === 'http:' && isLoopbackHostname(parsed.hostname))
}

function isAllowedPrivateLanHttpUrl(value) {
  const parsed = parseUrl(value)
  if (!parsed || parsed.protocol !== 'http:') return false
  const hostname = parsed.hostname.toLowerCase()
  return hostname.startsWith('192.168.')
    || hostname.startsWith('10.')
    || /^172\.(1[6-9]|2\d|3[01])\./.test(hostname)
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

  const parsed = parseUrl(value)
  const isHttps = parsed?.protocol === 'https:'
  const isCloudflareTunnel = value.includes('trycloudflare')
  const isLocalDirect = isLoopbackHttpUrl(value) || isAllowedPrivateLanHttpUrl(value)

  if (parsed?.protocol === 'http:' && !isHttps && !isCloudflareTunnel && !isLocalDirect) {
    return '生产环境禁止使用公网 http:// 明文协议。请改用 https://、Cloudflare 安全隧道或本地/局域网直连。'
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

  const parsed = parseUrl(value)
  if (!parsed) return false

  const isHttps = parsed.protocol === 'https:'
  const isCloudflareTunnel = value.includes('trycloudflare')
  const isLocalDirect = isLoopbackHttpUrl(value) || isAllowedPrivateLanHttpUrl(value)

  // Allowed: HTTPS, Cloudflare tunnel, loopback, or approved private-LAN HTTP.
  return isHttps || isCloudflareTunnel || isLocalDirect
}
