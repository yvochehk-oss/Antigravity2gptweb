<template>
  <header class="app-header" data-testid="executive-header">
    <!-- 第一排：品牌与高管标题 + 操作区 -->
    <div class="app-header__top-bar">
      <div class="app-header__brand">
        <h1 class="app-header__title">锐宝管理</h1>
        <span class="app-header__badge">高管专享</span>
      </div>

      <div class="app-header__actions">
        <button
          type="button"
          class="icon-button"
          :aria-pressed="privacyMode"
          :aria-label="privacyMode ? '显示敏感数据' : '隐藏敏感数据'"
          :title="privacyMode ? '显示敏感数据' : '隐藏敏感数据'"
          @click="togglePrivacy"
        >
          <svg v-if="!privacyMode" class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.75" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.75" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
          </svg>
          <svg v-else class="w-4 h-4 text-amber-300" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.75" d="M13.875 18.825A10.05 10.05 0 0112 19c-4.478 0-8.268-2.943-9.543-7a9.97 9.97 0 011.563-3.029m5.858.908a3 3 0 114.243 4.243M3 3l18 18" />
          </svg>
        </button>
        <button
          type="button"
          class="icon-button"
          aria-label="刷新经营数据"
          title="刷新经营数据"
          :disabled="loading"
          @click="handleRefresh"
        >
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true" :class="{ 'animate-spin': loading }">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.75" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9M20 20v-5h-.581m0 0a8.003 8.003 0 01-15.357-2" />
          </svg>
        </button>
      </div>
    </div>

    <!-- 第二排：状态单独一排 -->
    <div class="app-header__status-row">
      <div
        class="app-header__status-pill"
        :class="{
          'is-connected': connectionStatus === 'connected',
          'is-offline': connectionStatus === 'offline'
        }"
        :title="connectionModeText"
      >
        <span
          class="app-header__status-dot"
          :class="{
            'is-connected': connectionStatus === 'connected',
            'is-offline': connectionStatus === 'offline'
          }"
          aria-hidden="true"
        />
        <span class="app-header__status-text">
          {{ connectionModeText }}
        </span>
      </div>
    </div>
  </header>
</template>

<script setup>
import { storeToRefs } from 'pinia'
import { useUiStore } from '../../stores/ui.store'
import { hapticsLight } from '../../shared/utils/haptics'

const emit = defineEmits(['refresh'])

const ui = useUiStore()
const { privacyMode, loading, connectionStatus, connectionModeText } = storeToRefs(ui)

async function togglePrivacy() {
  privacyMode.value = !privacyMode.value
  await hapticsLight()
}

async function handleRefresh() {
  emit('refresh')
  await hapticsLight()
}
</script>
