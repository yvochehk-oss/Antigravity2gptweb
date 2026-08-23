<template>
  <main class="login-page flex min-h-[100dvh] items-center justify-center p-4 bg-gradient-to-b from-[#050b14] via-[#091424] to-[#040912]">
    <div class="w-full max-w-[420px] rounded-3xl border border-amber-400/30 bg-gradient-to-b from-[#14233a] to-[#0a1322] p-6 shadow-[0_20px_60px_rgba(0,0,0,0.65)] relative overflow-hidden backdrop-blur-md">
      <!-- 装饰背景光效 -->
      <div class="pointer-events-none absolute -right-16 -top-16 h-48 w-48 rounded-full bg-amber-400/10 blur-3xl" aria-hidden="true" />
      <div class="pointer-events-none absolute -left-16 -bottom-16 h-48 w-48 rounded-full bg-sky-500/10 blur-3xl" aria-hidden="true" />

      <form class="relative z-10" novalidate @submit.prevent="submit">
        <!-- 品牌标识与标题 -->
        <div class="text-center">
          <div class="mx-auto flex h-16 w-16 items-center justify-center rounded-2xl border-2 border-amber-400/60 bg-gradient-to-br from-amber-400 to-amber-600 font-serif text-3xl font-black text-slate-950 shadow-[0_8px_24px_rgba(226,185,99,0.4)]" aria-hidden="true">
            锐
          </div>
          <h1 class="mt-4 text-[24px] font-bold tracking-tight text-slate-50">
            锐宝管理
          </h1>
          <p class="mt-1 text-[13px] text-slate-400">
            高管财税穿透与经营智策决策系统
          </p>
        </div>

        <!-- 身份表单 -->
        <div class="mt-6 space-y-4">
          <div>
            <label for="username" class="block text-[13px] font-semibold text-slate-200">登录账号</label>
            <div class="relative mt-1.5">
              <input
                id="username"
                v-model="username"
                class="form-input text-[14px]"
                type="text"
                autocomplete="username"
                minlength="2"
                maxlength="40"
                required
                placeholder="请输入账号 (如: admin)"
                :aria-invalid="Boolean(errorMessage)"
                aria-describedby="login-error"
              />
            </div>
          </div>

          <div>
            <label for="password" class="block text-[13px] font-semibold text-slate-200">安全密码</label>
            <div class="relative mt-1.5">
              <input
                id="password"
                v-model="password"
                class="form-input text-[14px]"
                type="password"
                autocomplete="current-password"
                minlength="6"
                required
                placeholder="请输入密码"
                :aria-invalid="Boolean(errorMessage)"
              />
            </div>
          </div>
        </div>

        <button
          type="submit"
          class="primary-button mt-6 min-h-12 w-full text-[15px] font-bold shadow-[0_8px_24px_rgba(226,185,99,0.3)] transition-all hover:scale-[1.01] active:scale-[0.98]"
          :disabled="isSubmitting"
        >
          {{ isSubmitting ? '认证中...' : '进入锐宝管理系统' }}
        </button>

        <button
          v-if="isNative"
          type="button"
          @click="doBiometricLogin"
          class="mt-3 min-h-12 w-full rounded-xl border border-sky-400/30 bg-sky-400/10 text-[14px] font-bold text-sky-300 transition-all hover:bg-sky-400/20 active:bg-sky-400/30 flex items-center justify-center gap-2"
        >
          <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 11c0 3.517-1.009 6.799-2.753 9.571m-3.44-2.04l.054-.09A13.916 13.916 0 008 11a4 4 0 118 0c0 1.017-.07 2.019-.203 3m-2.118 6.844A21.88 21.88 0 0015.171 17m3.839 1.132c.645-2.266.99-4.659.99-7.132A8 8 0 008 4.07M3 15.364c.64-1.319 1-2.8 1-4.364 0-1.457.39-2.823 1.07-4" />
          </svg>
          刷脸 / 指纹快捷登录
        </button>

        <p id="login-note" class="mt-4 text-center text-[12px] leading-5 text-slate-400">
          已接入高管安全认证与身份识别中心。
        </p>

        <p v-if="errorMessage" id="login-error" class="mt-3 rounded-xl border border-rose-400/30 bg-rose-400/10 p-3 text-center text-[13px] font-medium text-rose-200" role="alert">
          {{ errorMessage }}
        </p>
      </form>
    </div>
  </main>
</template>

<script setup>
import { ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../../stores/auth.store'
import { Capacitor } from '@capacitor/core'
import { performBiometricLogin } from '../../api/biometric'

const auth = useAuthStore()
const router = useRouter()
const username = ref('')
const password = ref('')
const errorMessage = ref('')
const isSubmitting = ref(false)

const isNative = computed(() => Capacitor.isNativePlatform())

async function submit() {
  errorMessage.value = ''
  
  const normalizedUsername = username.value.trim()
  if (normalizedUsername.length < 2) {
    errorMessage.value = '请输入有效的账号。'
    return
  }
  if (password.value.length < 6) {
    errorMessage.value = '密码不能少于 6 位。'
    return
  }

  isSubmitting.value = true
  try {
    await auth.login({ username: normalizedUsername, password: password.value })
    router.replace({ name: 'dashboard' })
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登录失败，请稍后重试。'
  } finally {
    isSubmitting.value = false
  }
}

async function doBiometricLogin() {
  errorMessage.value = ''
  try {
    const creds = await performBiometricLogin()
    isSubmitting.value = true
    await auth.login({ username: creds.username, password: creds.password })
    router.replace({ name: 'dashboard' })
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '生物识别登录失败。'
  } finally {
    isSubmitting.value = false
  }
}
</script>
