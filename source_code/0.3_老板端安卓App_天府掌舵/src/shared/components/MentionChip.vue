<template>
  <button
    type="button"
    class="mention-chip"
    :class="`mention-chip--${chip.kind}`"
    :aria-label="`下钻查看 ${chip.label} (${chip.shortCode})`"
    @click="$emit('drilldown', chip)"
  >
    <span class="mention-chip__icon" aria-hidden="true">↳</span>
    <span class="mention-chip__text">{{ chip.shortCode }}</span>
    <span class="mention-chip__label">{{ chip.label }}</span>
  </button>
</template>

<script setup>
defineProps({
  chip: {
    type: Object,
    required: true
  }
})

defineEmits(['drilldown'])
</script>

<style scoped>
.mention-chip {
  display: inline-flex;
  min-height: 2.75rem;
  max-width: 100%;
  align-items: center;
  gap: 0.375rem;
  margin: 0 0.125rem;
  padding: 0.5rem 0.625rem;
  border: 1px solid rgba(217, 164, 65, 0.52);
  border-radius: 0.75rem;
  background: rgba(217, 164, 65, 0.12);
  color: #f5d58a;
  font-size: 0.8125rem;
  line-height: 1.25rem;
  font-weight: 600;
  vertical-align: middle;
  white-space: nowrap;
  overflow: hidden;
  text-align: left;
  touch-action: manipulation;
  transition: background-color 150ms ease, border-color 150ms ease, transform 150ms ease;
}

.mention-chip:hover {
  border-color: rgba(237, 189, 92, 0.8);
  background: rgba(217, 164, 65, 0.2);
}

.mention-chip:active {
  transform: scale(0.98);
}

.mention-chip:focus-visible {
  outline: 2px solid rgba(237, 189, 92, 0.95);
  outline-offset: 2px;
}

.mention-chip__icon {
  flex: 0 0 auto;
  color: #edbd5c;
  font-size: 0.875rem;
  line-height: 1.25rem;
}

.mention-chip__text {
  flex: 0 0 auto;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  letter-spacing: 0.01em;
}

.mention-chip__label {
  min-width: 0;
  overflow: hidden;
  color: #cbd5e1;
  font-size: 0.8125rem;
  line-height: 1.25rem;
  font-weight: 400;
  text-overflow: ellipsis;
}

@media (max-width: 410px) {
  .mention-chip__label {
    max-width: 8rem;
  }
}
</style>
