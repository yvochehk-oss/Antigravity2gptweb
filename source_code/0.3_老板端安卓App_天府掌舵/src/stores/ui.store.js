import { computed, ref, watch } from 'vue'
import { defineStore } from 'pinia'
import { isAllowedServerUrl, getUrlViolationReason, isProductionBuild } from '../config/server-policy'

const SERVER_URL_KEY = 'cdjg_server_url'
const SETTINGS_KEY = 'cdjg_executive_settings'
const DEFAULT_TAX_SERVER_URL = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8921'

function readStorageValue(key) {
  try {
    return globalThis.localStorage?.getItem(key) ?? null
  } catch {
    return null
  }
}

function writeStorageValue(key, value) {
  try {
    globalThis.localStorage?.setItem(key, value)
  } catch {
    // Safari private browsing and exhausted storage can reject Web Storage.
    // The in-memory store remains usable when persistence is unavailable.
  }
}

function readSettings() {
  try {
    return JSON.parse(readStorageValue(SETTINGS_KEY)) || {}
  } catch {
    return {}
  }
}

function getPageHostname() {
  try {
    return String(globalThis.location?.hostname || '').toLowerCase()
  } catch {
    return ''
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

function isPrivateLanHostname(hostname) {
  const value = String(hostname || '').trim().toLowerCase().replace(/^\[|\]$/g, '')
  const octets = value.split('.')

  if (octets.length === 4 && octets.every(octet => /^\d+$/.test(octet))) {
    const numbers = octets.map(Number)
    if (numbers.some(octet => octet < 0 || octet > 255)) return false

    return numbers[0] === 10
      || (numbers[0] === 172 && numbers[1] >= 16 && numbers[1] <= 31)
      || (numbers[0] === 192 && numbers[1] === 168)
      || (numbers[0] === 169 && numbers[1] === 254)
  }

  // RFC 4193 ULA and RFC 4291 link-local IPv6 addresses are local-network
  // addresses too. URL.hostname may include brackets for IPv6 literals.
  return /^f[cd][0-9a-f]{2}:/.test(value) || /^fe[89ab][0-9a-f]:/.test(value)
}

function isPrivateLanUrl(value) {
  try {
    return isPrivateLanHostname(new URL(value).hostname)
  } catch {
    return false
  }
}

function shouldPreferDefaultServerUrl(storedUrl) {
  return isLoopbackHostname(getPageHostname()) && isPrivateLanUrl(storedUrl)
}

function getInitialServerUrl(storedUrl) {
  if (shouldPreferDefaultServerUrl(storedUrl)) return DEFAULT_TAX_SERVER_URL
  return storedUrl || DEFAULT_TAX_SERVER_URL
}

export const useUiStore = defineStore('ui', () => {
  const settings = readSettings()
  const privacyMode = ref(Boolean(settings.privacyMode))
  const loading = ref(false)
  const connectionStatus = ref('connecting')
  const storedServerUrl = readStorageValue(SERVER_URL_KEY)
  const serverBaseUrl = ref(getInitialServerUrl(storedServerUrl))
  const offlineCacheEnabled = ref(settings.offlineCacheEnabled ?? true)

  // Tracks the last URL that passed the production whitelist check.
  // Reset to null on page load (only persisted URLs that were saved are valid).
  const lastAllowedServerUrl = ref(
    storedServerUrl && !shouldPreferDefaultServerUrl(storedServerUrl) && isAllowedServerUrl(storedServerUrl)
      ? storedServerUrl
      : null
  )

  /**
   * Detailed connection mode label for the settings page.
   * 'demo-mode'     — demo session active (auth store isDemoMode)
   * 'blocked-url'   — production build but URL fails whitelist check
   * 'allowed-url'   — production build with HTTPS/cloudflare URL
   * 'dev-http'      — development build with http:// URL (normal)
   * 'dev-https'     — development build with https:// URL
   */
  const connectionMode = computed(() => {
    const url = serverBaseUrl.value
    if (isProductionBuild() && !isAllowedServerUrl(url)) return 'blocked-url'
    if (url.startsWith('https://') || url.includes('trycloudflare')) return 'allowed-url'
    return 'dev-http'
  })

  const connectionModeText = computed(() => {
    if (connectionStatus.value === 'offline') return '⚠️ 远程服务器未连通 (离线快照模式)'
    if (connectionStatus.value === 'connecting') return '正在连接经营底账...'
    if (connectionMode.value === 'blocked-url') return '⚠️ 当前 URL 在生产环境不可用，请改用 HTTPS'
    if (serverBaseUrl.value.startsWith('https://') || serverBaseUrl.value.includes('trycloudflare')) {
      return '5G 远程加密直连 · 全网在线'
    }
    return '本地/局域网直连模式'
  })

  /**
   * Human-readable reason why the current URL is rejected in production.
   * Returns null when the URL is acceptable or running in DEV mode.
   */
  const urlViolationReason = computed(() => {
    if (!isProductionBuild()) return null
    return getUrlViolationReason(serverBaseUrl.value)
  })

  function persistSettings() {
    const url = serverBaseUrl.value
    if (isProductionBuild() && !isAllowedServerUrl(url)) {
      // Revert to the last known-good URL (or leave unchanged if none).
      if (lastAllowedServerUrl.value) {
        serverBaseUrl.value = lastAllowedServerUrl.value
      }
      connectionStatus.value = 'blocked-url'
      return
    }

    // URL is acceptable — record it as the last good value.
    if (isAllowedServerUrl(url)) {
      lastAllowedServerUrl.value = url
    }

    writeStorageValue(SERVER_URL_KEY, serverBaseUrl.value)
    writeStorageValue(SETTINGS_KEY, JSON.stringify({
      offlineCacheEnabled: offlineCacheEnabled.value,
      privacyMode: privacyMode.value
    }))
  }

  watch([offlineCacheEnabled, serverBaseUrl, privacyMode], persistSettings)

  return {
    privacyMode,
    loading,
    connectionStatus,
    serverBaseUrl,
    offlineCacheEnabled,
    connectionMode,
    connectionModeText,
    urlViolationReason,
    persistSettings
  }
})
