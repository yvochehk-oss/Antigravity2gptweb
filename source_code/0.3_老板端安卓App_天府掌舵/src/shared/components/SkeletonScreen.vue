<template>
  <div class="skeleton-screen" :class="{ 'skeleton-screen--inline': inline }" aria-busy="true" aria-label="内容加载中">
    <div v-if="type === 'card'" class="space-y-3">
      <div v-for="i in rows" :key="i" class="skeleton-card animate-pulse">
        <div class="skeleton-line skeleton-line--title" :style="{ width: `${40 + (i * 17) % 40}%` }" />
        <div class="skeleton-line" :style="{ width: `${55 + (i * 23) % 30}%` }" />
        <div class="skeleton-line skeleton-line--short" style="width: 30%" />
      </div>
    </div>

    <div v-else-if="type === 'kpi'" class="grid grid-cols-3 gap-3">
      <div v-for="i in Math.min(rows, 3)" :key="i" class="skeleton-kpi">
        <div class="skeleton-line skeleton-line--title" style="width: 60%" />
        <div class="skeleton-line skeleton-line--value" style="width: 80%" />
      </div>
    </div>

    <div v-else-if="type === 'list'" class="space-y-2">
      <div v-for="i in rows" :key="i" class="skeleton-list-item animate-pulse">
        <div class="skeleton-avatar" />
        <div class="flex-1 space-y-1.5">
          <div class="skeleton-line skeleton-line--title" :style="{ width: `${30 + (i * 13) % 40}%` }" />
          <div class="skeleton-line" :style="{ width: `${50 + (i * 19) % 30}%` }" />
        </div>
      </div>
    </div>

    <div v-else class="space-y-3">
      <div v-for="i in rows" :key="i" class="skeleton-line animate-pulse" :style="{ width: `${35 + (i * 11) % 50}%` }" />
    </div>
  </div>
</template>

<script setup>
defineProps({
  type: {
    type: String,
    default: 'text',
    validator: v => ['text', 'card', 'kpi', 'list'].includes(v)
  },
  rows: {
    type: Number,
    default: 4
  },
  inline: {
    type: Boolean,
    default: false
  }
})
</script>

<style scoped>
.skeleton-screen { @apply w-full; }
.skeleton-screen--inline { @apply inline-block w-auto; }

.skeleton-card {
  @apply bg-slate-800/50 rounded-xl p-4 border border-slate-700/50;
}
.skeleton-kpi {
  @apply bg-slate-800/50 rounded-xl p-4 border border-slate-700/50 flex flex-col gap-2;
}
.skeleton-list-item {
  @apply flex items-center gap-3 bg-slate-800/30 rounded-lg p-3;
}
.skeleton-avatar {
  @apply w-9 h-9 rounded-full bg-slate-700 flex-shrink-0;
}

.skeleton-line {
  @apply h-3 rounded bg-slate-700;
}
.skeleton-line--title  { @apply h-4; }
.skeleton-line--value  { @apply h-6; }
.skeleton-line--short  { @apply h-2; }

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50%       { opacity: 0.5; }
}
.animate-pulse { animation: pulse 1.5s ease-in-out infinite; }
</style>
