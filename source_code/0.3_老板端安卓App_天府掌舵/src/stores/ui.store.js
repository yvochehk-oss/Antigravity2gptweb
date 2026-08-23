import { computed, ref, watch } from 'vue'
import { defineStore } from 'pinia'
import { isAllowedServerUrl, getUrlViolationReason, isProductionBuild } from '../config/server-policy'

const SERVER_URL_KEY = 'cdjg_server_url'
const SETTINGS_KEY = 'cdjg_executive_settings'
const DEFAULT_SERVER_URL = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8922'

function readSettings() {
  try {
    return JSON.parse(localStorage.getItem(SETTINGS_KEY)) || {}
  } catch {
    return {}
  }
}

export const useUiStore = defineStore('ui', () => {
  const settings = readSettings()
  const privacyMode = ref(Boolean(settings.privacyMode))
  const loading = ref(false)
  const connectionStatus = ref('connecting')
  const serverBaseUrl = ref(localStorage.getItem(SERVER_URL_KEY) || DEFAULT_SERVER_URL)
  const offlineCacheEnabled = ref(settings.offlineCacheEnabled ?? true)

  // Tracks the last URL that passed the production whitelist check.
  // Reset to null on page load (only persisted URLs that were saved are valid).
  const lastAllowedServerUrl = ref(
    localStorage.getItem(SERVER_URL_KEY) && isAllowedServerUrl(localStorage.getItem(SERVER_URL_KEY))
      ? localStorage.getItem(SERVER_URL_KEY)
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

    localStorage.setItem(SERVER_URL_KEY, serverBaseUrl.value)
    localStorage.setItem(SETTINGS_KEY, JSON.stringify({
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
