<template>
  <div class="app-shell luxury-bg flex min-h-screen flex-col" :class="{ 'is-copilot-shell': isCopilot }">
    <ExecutiveHeader @refresh="refresh" />
    <div class="app-content flex-1" :class="{ 'is-copilot-content': isCopilot }">
      <RouterView v-slot="{ Component }">
        <keep-alive :exclude="['CopilotView', 'LoginView', 'SettingsView']">
          <component :is="Component" />
        </keep-alive>
      </RouterView>
    </div>
    <ProjectDrilldown />
    <BottomNavigation />
  </div>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useExecutiveStore } from '../stores/executive.store'
import BottomNavigation from '../shared/components/BottomNavigation.vue'
import ExecutiveHeader from '../shared/components/ExecutiveHeader.vue'
import ProjectDrilldown from '../modules/projects/components/ProjectDrilldown.vue'

const route = useRoute()
const executive = useExecutiveStore()
const isCopilot = computed(() => route?.name === 'copilot' || route?.path === '/copilot')

async function refresh() {
  try {
    await executive.refresh()
  } catch {
    console.warn('offline mode activated')
  }
}

onMounted(refresh)
</script>
