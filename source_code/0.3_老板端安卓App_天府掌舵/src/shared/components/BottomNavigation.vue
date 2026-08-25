<template>
  <div
    v-if="visibleTabs.length"
    class="bottom-navigation-wrap"
    :class="{ 'is-dimmed': isModalOpen }"
    :aria-hidden="isModalOpen"
  >
    <nav class="bottom-navigation" :class="{ 'is-dimmed': isModalOpen }" aria-label="主导航">
      <component
        :is="isModalOpen ? 'div' : 'RouterLink'"
        v-for="tab in visibleTabs"
        :key="tab.name"
        :to="isModalOpen ? undefined : { name: tab.name }"
        class="bottom-navigation__item"
        :class="{
          'is-active': route.name === tab.name,
          'bottom-navigation__item--ai': tab.name === 'copilot',
          'is-disabled': isModalOpen
        }"
        :tabindex="isModalOpen ? -1 : 0"
        :aria-disabled="isModalOpen"
        :aria-label="tab.label"
      >
        <template v-if="tab.name === 'copilot'">
          <div class="ai-bubble-container" :class="{ 'is-active': route.name === 'copilot' }">
            <span class="ai-breathing-aura" aria-hidden="true" />
            <div class="ai-button-bubble">
              <svg class="ai-icon" fill="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                <path d="M13 10V3L4 14h7v7l9-11h-7z" />
              </svg>
            </div>
          </div>
          <span class="nav-label nav-label--ai">
            {{ tab.label }}
          </span>
        </template>
        <template v-else>
          <svg class="bottom-navigation__icon" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.85" :d="tab.icon" />
          </svg>
          <span class="nav-label">{{ tab.label }}</span>
        </template>
      </component>
    </nav>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { storeToRefs } from 'pinia'
import { PERMISSIONS, useAuthStore } from '../../stores/auth.store'
import { useExecutiveStore } from '../../stores/executive.store'

const route = useRoute()
const auth = useAuthStore()
const executive = useExecutiveStore()
const { activeProject360 } = storeToRefs(executive)

const isModalOpen = computed(() => Boolean(activeProject360.value))

// AI 智策居中 (5个导航项中的第3项)，视觉突出且常驻呼吸闪动
const tabs = [
  { name: 'dashboard', label: '集团大盘', permission: PERMISSIONS.VIEW_COCKPIT, icon: 'M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14' },
  { name: 'projects', label: '项目穿透', permission: PERMISSIONS.VIEW_PROJECTS, icon: 'M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2M3 21h2m4-14h1m-1 4h1m4-4h1m-1 4h1' },
  { name: 'copilot', label: 'AI 智策', permission: PERMISSIONS.USE_COPILOT, icon: 'M13 10V3L4 14h7v7l9-11h-7z' },
  { name: 'companies', label: '项目全览', permission: PERMISSIONS.VIEW_COMPANIES, icon: 'M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM14 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zM14 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z' },
  { name: 'settings', label: '系统设置', permission: PERMISSIONS.MANAGE_SETTINGS, icon: 'M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066 1.724 1.724 0 002.37 2.37 1.724 1.724 0 001.065 2.572 1.724 1.724 0 000 3.35 1.724 1.724 0 00-1.066 2.573 1.724 1.724 0 01-2.37 2.37 1.724 1.724 0 00-2.572 1.065 1.724 1.724 0 01-3.35 0 1.724 1.724 0 00-2.573-1.066 1.724 1.724 0 01-2.37-2.37 1.724 1.724 0 00-1.065-2.572 1.724 1.724 0 010-3.35 1.724 1.724 0 001.066-2.573 1.724 1.724 0 012.37-2.37 1.724 1.724 0 002.572-1.065zM15 12a3 3 0 11-6 0 3 3 0 016 0z' }
]

const visibleTabs = computed(() => tabs.filter(tab => auth.can(tab.permission)))
</script>
