<template>
  <span
    class="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[12px] font-semibold shadow-sm"
    :class="meta.classes"
    role="status"
    :aria-label="meta.ariaLabel"
  >
    <span aria-hidden="true">{{ meta.icon }}</span>
    <span>{{ meta.label }}</span>
  </span>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  /**
   * Data source mode.
   * 'live'        — real API data
   * 'demo'        — default mock data
   * 'snapshot'    — loaded from offline snapshot
   * 'unavailable' — no data loaded
   */
  mode: {
    type: String,
    default: 'unavailable',
    validator: v => ['live', 'demo', 'snapshot', 'unavailable'].includes(v)
  },
  /** Optional timestamp shown for snapshot mode. */
  snapshotTime: {
    type: String,
    default: ''
  }
})

const meta = computed(() => {
  switch (props.mode) {
    case 'live':
      return {
        icon: '●',
        label: '实时',
        classes: 'border-emerald-400/35 bg-emerald-400/15 text-emerald-200',
        ariaLabel: '实时数据'
      }
    case 'demo':
      return {
        icon: '▲',
        label: 'DEMO 数据',
        classes: 'border-amber-400/35 bg-amber-400/15 text-amber-200',
        ariaLabel: '演示数据'
      }
    case 'snapshot':
      return {
        icon: '⏱',
        label: props.snapshotTime
          ? `离线快照 (${props.snapshotTime})`
          : '离线快照',
        classes: 'border-slate-400/35 bg-slate-400/15 text-slate-300',
        ariaLabel: '离线快照数据'
      }
    case 'unavailable':
    default:
      return {
        icon: '✕',
        label: '不可用',
        classes: 'border-rose-400/35 bg-rose-400/15 text-rose-200',
        ariaLabel: '数据不可用'
      }
  }
})
</script>
