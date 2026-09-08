<template>
  <main class="app-page px-4 py-4 pb-8 space-y-4 max-w-[440px] mx-auto">
    <section aria-labelledby="settings-title" class="space-y-1">
      <h1 id="settings-title" class="page-title text-slate-50 font-bold">经营底账穿透设置</h1>
      <p class="text-[13px] leading-5 text-slate-400">配置底层 API 链路服务地址、远程 HTTPS 安全隧道与离线快照模式。</p>
    </section>

    <!-- 数据源配置卡 -->
    <section
      aria-labelledby="connection-settings-title"
      class="surface-card rounded-2xl p-4 shadow-lg"
    >
      <header class="border-b border-white/10 pb-3">
        <div class="flex items-center justify-between gap-3">
          <div>
            <h2 id="connection-settings-title" class="text-[16px] leading-6 font-bold text-slate-50">
              远程穿透服务与 API 节点
            </h2>
          </div>
          <span class="rounded-md border border-amber-400/30 bg-amber-400/10 px-2 py-0.5 font-mono text-[11px] font-bold text-amber-200">
            v2.4
          </span>
        </div>
      </header>

      <form class="mt-4 space-y-3.5" @submit.prevent="saveAndTest">
        <div>
          <label for="server-url" class="block text-[13px] font-semibold text-slate-200">
            后端 API 服务地址
          </label>
          <p id="server-url-help" class="mt-0.5 text-[12px] leading-4 text-slate-400">
            支持局域网 IP 直连或 Cloudflare 安全内网穿透隧道。
            <span v-if="isProduction" class="mt-0.5 block text-rose-300">生产环境仅支持 HTTPS 或 trycloudflare 隧道地址。</span>
          </p>
          <input
            id="server-url"
            v-model="serverBaseUrl"
            type="url"
            inputmode="url"
            autocomplete="url"
            spellcheck="false"
            placeholder="https://your-tunnel.trycloudflare.com"
            aria-describedby="server-url-help server-url-error"
            :aria-invalid="Boolean(urlError)"
            class="form-input mt-2 text-[14px]"
            @input="clearFeedback"
          />
          <p v-if="urlError" id="server-url-error" class="mt-1.5 text-[12px] text-rose-300 font-medium" role="alert">
            {{ urlError }}
          </p>
        </div>

        <div class="flex items-center justify-between gap-3 rounded-xl border border-white/10 bg-black/25 px-3.5 py-2.5">
          <div class="min-w-0">
            <p class="text-[11px] font-medium text-slate-400">当前链路路由</p>
            <p class="mt-0.5 break-words text-[13px] font-semibold text-slate-100">{{ connectionModeLabel }}</p>
          </div>
          <span
            class="flex shrink-0 items-center gap-1.5 rounded-full border px-2.5 py-1 text-[12px] font-semibold shadow-sm"
            :class="connectionMeta.classes"
            role="status"
            aria-live="polite"
          >
            <span class="h-2 w-2 rounded-full" :class="connectionMeta.dotClass" aria-hidden="true" />
            {{ connectionMeta.label }}
          </span>
        </div>

        <div class="rounded-xl border border-amber-400/25 bg-amber-400/10 px-3.5 py-2.5">
          <p class="text-[12px] font-semibold text-amber-200">安全使用指引</p>
          <p class="mt-0.5 text-[12px] leading-5 text-slate-300">
            在管理台启动 Cloudflare 隧道后，将生成的专属加密 HTTPS 地址配置于此，即可实现外出随时审阅高管底账。
          </p>
        </div>

        <div
          v-if="urlViolationReason"
          class="rounded-xl border border-rose-400/35 bg-rose-400/15 px-3.5 py-2.5"
          role="alert"
          aria-live="assertive"
        >
          <p class="text-[12px] font-semibold text-rose-200">生产环境 URL 限制</p>
          <p class="mt-0.5 text-[12px] leading-5 text-rose-100">{{ urlViolationReason }}</p>
        </div>

        <button
          type="submit"
          :disabled="loading || testing"
          class="primary-button min-h-11 w-full text-[14px] font-bold"
        >
          <span v-if="testing" class="inline-block h-4 w-4 animate-spin rounded-full border-2 border-slate-950/30 border-t-slate-950 mr-1.5 align-middle" />
          <span>{{ testing ? '正在验证 API 链路…' : '保存并测试连接' }}</span>
        </button>

        <p
          v-if="feedback"
          class="rounded-xl border p-3 text-[13px] font-medium"
          :class="feedback.ok ? 'border-emerald-400/30 bg-emerald-400/10 text-emerald-200' : 'border-amber-400/30 bg-amber-400/10 text-amber-200'"
          role="status"
          aria-live="polite"
        >
          {{ feedback.message }}
        </p>
      </form>
    </section>

    <!-- 本地离线快照 -->
    <section
      aria-labelledby="offline-settings-title"
      class="surface-card rounded-2xl p-4 shadow-lg"
    >
      <header class="border-b border-white/10 pb-3">
        <h2 id="offline-settings-title" class="text-[16px] leading-6 font-bold text-slate-50">离线底账快照与数据冗余</h2>
      </header>

      <label for="offline-cache" class="mt-3.5 flex min-h-12 cursor-pointer items-center justify-between gap-4">
        <span class="min-w-0">
          <span class="block text-[14px] font-semibold text-slate-100">启用本地经营数据快照</span>
          <span class="mt-0.5 block text-[12px] leading-4 text-slate-400">地下车库或飞机离线等弱网场景下，仍可调阅上一次完整核验的经营账册。</span>
        </span>
        <span class="relative inline-flex h-7 w-12 shrink-0 items-center">
          <input id="offline-cache" v-model="offlineCacheEnabled" type="checkbox" class="peer sr-only" />
          <span class="absolute inset-0 rounded-full border border-[#52647f] bg-[#18263a] transition-colors peer-checked:border-amber-400 peer-checked:bg-amber-400 peer-focus-visible:ring-2 peer-focus-visible:ring-amber-400/80" />
          <span class="absolute left-1 h-5 w-5 rounded-full bg-slate-200 shadow-md transition-transform peer-checked:translate-x-5 peer-checked:bg-[#0a1422]" />
        </span>
      </label>
      <div class="mt-3 rounded-xl border border-white/10 bg-black/25 px-3.5 py-2.5 text-[12px] font-medium text-slate-300 flex items-center justify-between" role="status">
        <span>当前快照状态</span>
        <span class="font-bold" :class="offlineCacheEnabled ? 'text-amber-300' : 'text-slate-400'">{{ offlineCacheEnabled ? '已开启离线高密快照' : '仅使用实时联机' }}</span>
      </div>
    </section>

    <!-- 安全与认证设置 -->
    <section
      aria-labelledby="security-settings-title"
      class="surface-card rounded-2xl p-4 shadow-lg mt-4"
    >
      <header class="border-b border-white/10 pb-3 flex items-center justify-between">
        <h2 id="security-settings-title" class="text-[16px] leading-6 font-bold text-slate-50">账户与安全认证</h2>
      </header>
      
      <div v-if="bioAvailable">
        <label for="bio-cache" class="mt-4 mb-4 flex cursor-pointer items-center justify-between gap-4">
          <span class="min-w-0">
            <span class="block text-[14px] font-semibold text-slate-100">启用面容 / 指纹解锁</span>
            <span class="mt-0.5 block text-[12px] leading-4 text-slate-400">下次打开 App 时，无需输入密码即可快速验证身份进入。</span>
          </span>
          <span class="relative inline-flex h-7 w-12 shrink-0 items-center">
            <input id="bio-cache" :checked="bioEnabled" type="checkbox" @change="toggleBiometric" class="peer sr-only" />
            <span class="absolute inset-0 rounded-full border border-[#52647f] bg-[#18263a] transition-colors peer-checked:border-amber-400 peer-checked:bg-amber-400 peer-focus-visible:ring-2 peer-focus-visible:ring-amber-400/80" />
            <span class="absolute left-1 h-5 w-5 rounded-full bg-slate-200 shadow-md transition-transform peer-checked:translate-x-5 peer-checked:bg-[#0a1422]" />
          </span>
        </label>
        <div class="h-px bg-white/10 mb-4"></div>
      </div>

      <form class="mt-4 space-y-3.5" @submit.prevent="changePassword">
        <div>
          <label for="old-password" class="block text-[13px] font-semibold text-slate-200">当前密码</label>
          <input
            id="old-password"
            v-model="pwdForm.oldPassword"
            type="password"
            required
            placeholder="请输入当前密码"
            class="form-input mt-2 text-[14px]"
          />
        </div>
        <div>
          <label for="new-password" class="block text-[13px] font-semibold text-slate-200">新密码</label>
          <input
            id="new-password"
            v-model="pwdForm.newPassword"
            type="password"
            minlength="6"
            required
            placeholder="不少于 6 个字符"
            class="form-input mt-2 text-[14px]"
          />
        </div>
        <button
          type="submit"
          :disabled="pwdLoading"
          class="primary-button min-h-11 w-full text-[14px] font-bold"
        >
          <span>{{ pwdLoading ? '正在更新密码…' : '更新安全密码' }}</span>
        </button>

        <p
          v-if="pwdFeedback"
          class="rounded-xl border p-3 text-[13px] font-medium"
          :class="pwdFeedback.ok ? 'border-emerald-400/30 bg-emerald-400/10 text-emerald-200' : 'border-rose-400/30 bg-rose-400/10 text-rose-200'"
          role="status"
          aria-live="polite"
        >
          {{ pwdFeedback.message }}
        </p>
      </form>
    </section>

    <div class="pt-6">
      <button
        type="button"
        @click="logout"
        class="w-full rounded-xl border border-rose-400/20 bg-rose-400/10 px-4 py-3.5 text-[14px] font-bold text-rose-300 transition-colors hover:bg-rose-400/20 active:bg-rose-400/30"
      >
        退出当前账号
      </button>
    </div>
  </main>
</template>

<script setup>
import { computed, ref, onMounted } from 'vue'
import { storeToRefs } from 'pinia'
import { useExecutiveStore } from '../../stores/executive.store'
import { useUiStore } from '../../stores/ui.store'
import { useAuthStore } from '../../stores/auth.store'
import { isProductionBuild, isAllowedServerUrl, getUrlViolationReason } from '../../config/server-policy'
import { isBiometricAvailable, enableBiometric } from '../../api/biometric'
import { Preferences } from '@capacitor/preferences'

const ui = useUiStore()
const executive = useExecutiveStore()
const { serverBaseUrl, offlineCacheEnabled, connectionStatus, loading, urlViolationReason } = storeToRefs(ui)
const feedback = ref(null)
const urlError = ref('')
const testing = ref(false)

const isProduction = computed(() => isProductionBuild())

const pwdForm = ref({ oldPassword: '', newPassword: '' })
const pwdLoading = ref(false)
const pwdFeedback = ref(null)

const bioAvailable = ref(false)
const bioEnabled = ref(false)

onMounted(async () => {
  bioAvailable.value = await isBiometricAvailable()
  if (bioAvailable.value) {
    const { value } = await Preferences.get({ key: 'cdjg_biometric_enabled' })
    bioEnabled.value = value === 'true'
  }
})

async function toggleBiometric(event) {
  // Read the checkbox state from the change event as well as the ref.  The
  // DOM event is the source of truth for this action; relying only on the ref
  // can observe the previous value in some runtimes.
  const shouldEnable = event?.target?.checked ?? bioEnabled.value
  if (shouldEnable) {
    try {
      const auth = useAuthStore()
      const session = auth.session
      const userId = session?.user?.id
      const currentPassword = pwdForm.value.oldPassword

      // Binding biometric credentials must be an explicit, authenticated
      // action.  Never manufacture a password when the current password is
      // absent: doing so would either create unusable credentials or, worse,
      // bind a known default secret to the device.
      if (
        !String(session?.accessToken || '').trim() ||
        userId === null ||
        userId === undefined ||
        String(userId).trim() === ''
      ) {
        throw new Error('请先正常登录后再启用生物识别。')
      }
      if (typeof currentPassword !== 'string' || currentPassword.trim() === '') {
        throw new Error('请输入当前密码后再启用生物识别。')
      }

      await enableBiometric(String(userId), currentPassword)
      bioEnabled.value = true
      await Preferences.set({ key: 'cdjg_biometric_enabled', value: 'true' })
      pwdFeedback.value = { ok: true, message: '生物识别已启用。' }
    } catch (err) {
      bioEnabled.value = false
      if (event?.target) event.target.checked = false
      pwdFeedback.value = { ok: false, message: err?.message || '生物识别启用失败，请稍后重试。' }
    }
  } else {
    bioEnabled.value = false
    await Preferences.remove({ key: 'cdjg_biometric_enabled' })
    await Preferences.remove({ key: 'cdjg_biometric_password' })
  }
}

const isRemote = computed(() => {
  const value = String(serverBaseUrl.value || '').trim()
  return value.startsWith('https://') || value.includes('trycloudflare')
})

const connectionModeLabel = computed(() => isRemote.value ? 'HTTPS 远程安全隧道' : '本机 / 局域网直连')

const connectionMeta = computed(() => {
  if (connectionStatus.value === 'connected') {
    return { label: '已连接', classes: 'border-emerald-400/35 bg-emerald-400/15 text-emerald-200', dotClass: 'bg-emerald-300' }
  }
  if (connectionStatus.value === 'offline') {
    return { label: '离线快照', classes: 'border-amber-400/35 bg-amber-400/15 text-amber-200', dotClass: 'bg-amber-300' }
  }
  return { label: '连接中', classes: 'border-sky-400/35 bg-sky-400/15 text-sky-200', dotClass: 'bg-sky-300 animate-pulse' }
})

function clearFeedback() {
  feedback.value = null
  urlError.value = ''
}

function validateServerUrl() {
  const value = String(serverBaseUrl.value || '').trim()
  if (!value) {
    urlError.value = '请输入后端 API 服务地址。'
    return false
  }
  try {
    const parsed = new URL(value)
    if (!['http:', 'https:'].includes(parsed.protocol) || !parsed.hostname) {
      throw new Error('unsupported protocol')
    }
  } catch {
    urlError.value = '地址格式不正确，请输入 http:// 或 https:// 开头的完整地址。'
    return false
  }

  // In production, check against the server policy whitelist.
  if (isProductionBuild() && !isAllowedServerUrl(value)) {
    urlError.value = getUrlViolationReason(value) || '当前地址在生产环境不可用，请改用 HTTPS 地址。'
    return false
  }

  return true
}

async function saveAndTest() {
  if (testing.value || loading.value || !validateServerUrl()) return
  serverBaseUrl.value = String(serverBaseUrl.value).trim()
  feedback.value = null
  ui.persistSettings()
  testing.value = true
  try {
    const isOnline = await fetch(`${serverBaseUrl.value}/login`)
      .then(res => res.status < 500)
      .catch(() => false)
    if (isOnline) {
      try { await executive.refresh() } catch {}
      ui.connectionStatus = 'connected'
      feedback.value = { ok: true, message: '配置已保存，服务器链路连通正常。' }
    } else {
      await executive.refresh()
      feedback.value = { ok: true, message: '配置已保存，经营数据链路连接正常。' }
    }
  } catch {
    feedback.value = { ok: false, message: '配置已保存，当前未连通远程服务器，已切换为离线快照模式。' }
  } finally {
    testing.value = false
  }
}

async function changePassword() {
  if (pwdLoading.value) return
  pwdLoading.value = true
  pwdFeedback.value = null
  
  try {
    const response = await fetch(`${ui.serverBaseUrl}/api/v1/auth/change-password`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${useAuthStore().session?.accessToken}`
      },
      body: JSON.stringify({
        old_password: pwdForm.value.oldPassword,
        new_password: pwdForm.value.newPassword
      })
    })

    if (!response.ok) {
      const err = await response.json().catch(() => ({}))
      throw new Error(err.detail || '密码修改失败')
    }

    pwdFeedback.value = { ok: true, message: '密码修改成功，请妥善保管新密码。' }
    pwdForm.value.oldPassword = ''
    pwdForm.value.newPassword = ''
  } catch (error) {
    pwdFeedback.value = { ok: false, message: error.message }
  } finally {
    pwdLoading.value = false
  }
}

function logout() {
  useAuthStore().logout()
  window.location.reload()
}
</script>
