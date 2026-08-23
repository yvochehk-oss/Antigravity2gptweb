const PREFIX = 'cdjg_snapshot_'
export const SNAPSHOT_SCHEMA_VERSION = 1
export const DEFAULT_SNAPSHOT_MAX_AGE_MS = 24 * 60 * 60 * 1000
const DEVICE_KEY_STORAGE_KEY = 'cdjg_snapshot_device_key'

/**
 * 明确声明为非敏感的字段白名单。
 * 只快照此列表中的字段，核心财务数据（real_profit, net_cashflow,
 * revenue, tax_burden, contract_amount 等）不在此列表中。
 */
const SNAPSHOT_WHITELIST = [
  'project_code',
  'name',
  'status',
  'tax_method',
  'progress_pct',
  'collection_rate',
  'region',
  'industry',
  'founded_year',
  'employee_count',
  'last_updated',
  'version'
]

/**
 * 检查是否可以进行安全加密（WebCrypto 可用且密钥已初始化）。
 */
function canUseSecureCrypto() {
  if (typeof window === 'undefined') return false
  if (!window.crypto?.subtle) return false
  try {
    // 检查密钥是否已生成且可用
    const stored = window.sessionStorage.getItem(DEVICE_KEY_STORAGE_KEY)
    return Boolean(stored)
  } catch {
    return false
  }
}

function bytesToBase64(bytes) {
  if (typeof btoa === 'function') {
    let binary = ''
    for (let i = 0; i < bytes.length; i += 1) binary += String.fromCharCode(bytes[i])
    return btoa(binary)
  }
  return Buffer.from(bytes).toString('base64')
}

function base64ToBytes(value) {
  if (typeof atob === 'function') {
    const binary = atob(value)
    const bytes = new Uint8Array(binary.length)
    for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i)
    return bytes
  }
  return new Uint8Array(Buffer.from(value, 'base64'))
}

function encodeUtf8(value) {
  if (typeof btoa === 'function') {
    return btoa(unescape(encodeURIComponent(value)))
  }
  return Buffer.from(value, 'utf-8').toString('base64')
}

function decodeUtf8(value) {
  if (typeof atob === 'function') {
    return decodeURIComponent(escape(atob(value)))
  }
  return Buffer.from(value, 'base64').toString('utf-8')
}

export function getOrCreateDeviceKey() {
  if (typeof window === 'undefined') return null
  // Key lives in sessionStorage so it is cleared when the tab/browser closes.
  // This limits the exposure window vs. localStorage. The ciphertext still
  // lives in localStorage under a role-scoped namespace.
  // Key is required for secure snapshots; if unavailable, snapshots are disabled.
  try {
    const stored = window.sessionStorage.getItem(DEVICE_KEY_STORAGE_KEY)
    if (stored) return stored
  } catch {
    // fall through to regeneration
  }
  const generated = generateUuid()
  try {
    window.sessionStorage.setItem(DEVICE_KEY_STORAGE_KEY, generated)
  } catch {
    // persistence failure is non-fatal; key remains valid for this session
  }
  return generated
}

function generateUuid() {
  if (typeof window !== 'undefined' && window.crypto?.randomUUID) {
    return window.crypto.randomUUID()
  }
  const bytes = new Uint8Array(16)
  if (typeof window !== 'undefined' && window.crypto?.getRandomValues) {
    window.crypto.getRandomValues(bytes)
  } else {
    for (let i = 0; i < bytes.length; i += 1) bytes[i] = Math.floor(Math.random() * 256)
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const hex = [...bytes].map(b => b.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

let cachedCryptoKey = null
async function importDeviceKey(rawKey) {
  if (cachedCryptoKey) return cachedCryptoKey
  const material = new TextEncoder().encode(rawKey.padEnd(32, '0').slice(0, 32))
  cachedCryptoKey = await window.crypto.subtle.importKey('raw', material, { name: 'AES-GCM' }, false, ['encrypt', 'decrypt'])
  return cachedCryptoKey
}

async function encryptAesGcm(data) {
  const json = JSON.stringify(data)
  const iv = window.crypto.getRandomValues(new Uint8Array(12))
  const key = await importDeviceKey(getOrCreateDeviceKey())
  const cipherBytes = new Uint8Array(await window.crypto.subtle.encrypt(
    { name: 'AES-GCM', iv },
    key,
    new TextEncoder().encode(json)
  ))
  return { iv: bytesToBase64(iv), cipher: bytesToBase64(cipherBytes) }
}

async function decryptAesGcm(ivBase64, cipherBase64) {
  const iv = base64ToBytes(ivBase64)
  const cipherBytes = base64ToBytes(cipherBase64)
  const key = await importDeviceKey(getOrCreateDeviceKey())
  const plainBytes = new Uint8Array(await window.crypto.subtle.decrypt(
    { name: 'AES-GCM', iv },
    key,
    cipherBytes
  ))
  const json = new TextDecoder().decode(plainBytes)
  return JSON.parse(json)
}

export function storageAvailable() {
  return typeof window !== 'undefined' && Boolean(window.localStorage)
}

/**
 * 使用 AES-GCM 加密数据。
 * 无法安全加密时返回 null（不再退化到 XOR）。
 */
export async function encryptData(data) {
  if (!canUseSecureCrypto()) {
    return null
  }
  try {
    return await encryptAesGcm(data)
  } catch {
    return null
  }
}

/**
 * 使用 AES-GCM 解密数据。
 * 无法安全解密时返回 null。
 */
export function decryptData(payload) {
  if (!payload || typeof payload !== 'object') return Promise.resolve(null)
  if (payload.iv && payload.cipher && canUseSecureCrypto()) {
    return decryptAesGcm(payload.iv, payload.cipher).catch(() => null)
  }
  // 无法在不安全状态下解密
  return Promise.resolve(null)
}

/**
 * 只快照白名单中的非敏感字段。
 * 与黑名单不同，白名单确保只有明确声明为安全的字段被保存。
 */
export function sanitizeSnapshot(data) {
  if (!data || typeof data !== 'object') return data
  const sanitized = Array.isArray(data) ? [] : {}
  for (const key of Object.keys(data)) {
    if (SNAPSHOT_WHITELIST.includes(key)) {
      const value = data[key]
      if (value && typeof value === 'object' && !Array.isArray(value)) {
        sanitized[key] = sanitizeSnapshot(value)
      } else {
        sanitized[key] = value
      }
    }
  }
  return sanitized
}

/**
 * Builds a role-scoped snapshot key so a CFO's cached cockpit does not
 * accidentally appear for a PROJECT_MANAGER who has no cockpit permission.
 *
 * Falls back to the flat key if no user context is available (e.g. SSR
 * environments or pre-login snapshots).
 */
export function scopedSnapshotKey(baseKey, userId, role) {
  if (userId && role) {
    return `${PREFIX}${baseKey}::uid=${userId}::role=${role}`
  }
  return `${PREFIX}${baseKey}`
}

export async function saveSnapshot(baseKey, value, userId, role) {
  if (!storageAvailable()) return false
  // 无法安全加密时不保存任何快照（禁用 XOR fallback）
  if (!canUseSecureCrypto()) {
    console.warn('[snapshot] Secure crypto unavailable, skipping snapshot')
    return false
  }
  try {
    const storageKey = scopedSnapshotKey(baseKey, userId, role)
    const sanitized = sanitizeSnapshot(value)
    const payload = {
      value: sanitized,
      savedAt: new Date().toISOString(),
      schema: SNAPSHOT_SCHEMA_VERSION,
      uid: userId ?? null,
      role: role ?? null
    }
    const encrypted = await encryptData(payload)
    if (!encrypted) {
      console.warn('[snapshot] Encryption failed, skipping snapshot')
      return false
    }
    const stored = {
      ...encrypted,
      savedAt: payload.savedAt,
      schema: SNAPSHOT_SCHEMA_VERSION,
      uid: payload.uid,
      role: payload.role
    }
    localStorage.setItem(storageKey, JSON.stringify(stored))
    return true
  } catch {
    return false
  }
}

export async function readSnapshot(baseKey, fallback, userId, role, maxAgeMs = DEFAULT_SNAPSHOT_MAX_AGE_MS) {
  if (!storageAvailable()) return fallback
  try {
    const storageKey = scopedSnapshotKey(baseKey, userId, role)
    const raw = JSON.parse(localStorage.getItem(storageKey))
    if (!raw) return fallback
    if (raw.schema && raw.schema > SNAPSHOT_SCHEMA_VERSION) {
      localStorage.removeItem(storageKey)
      return fallback
    }
    const savedAt = Date.parse(raw?.savedAt)
    const isFresh = Number.isFinite(savedAt) && Date.now() - savedAt <= maxAgeMs
    if (!isFresh) {
      localStorage.removeItem(storageKey)
      return fallback
    }
    const decrypted = await decryptData(raw)
    if (decrypted && typeof decrypted === 'object' && 'value' in decrypted) {
      return decrypted.value ?? fallback
    }
    return fallback
  } catch {
    return fallback
  }
}

export function snapshotSavedAt(baseKey, userId, role) {
  if (!storageAvailable()) return null
  try {
    const storageKey = scopedSnapshotKey(baseKey, userId, role)
    return JSON.parse(localStorage.getItem(storageKey))?.savedAt ?? null
  } catch {
    return null
  }
}

export function clearSnapshots() {
  if (!storageAvailable()) return
  const snapshotKeys = []
  for (let index = 0; index < localStorage.length; index += 1) {
    const key = localStorage.key(index)
    if (key?.startsWith(PREFIX)) snapshotKeys.push(key)
  }
  snapshotKeys.forEach(key => localStorage.removeItem(key))
}
