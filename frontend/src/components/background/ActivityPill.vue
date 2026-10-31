<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { useBackgroundStore } from '../../stores/background'
import ActivityGrid from './ActivityGrid.vue'

const store = useBackgroundStore()
const open = ref(false)
const root = ref<HTMLElement | null>(null)
const pillBtn = ref<HTMLButtonElement | null>(null)
// Fixed, not absolute: the library's centre column clips overflow, and at
// 768-900 px it is narrower than the grid. Placed under the pill, clamped to
// the viewport. Below 768 px the stylesheet's full-width rule applies.
const dropStyle = ref<Record<string, string>>({})
function place() {
  const r = pillBtn.value?.getBoundingClientRect()
  if (!r || window.innerWidth < 768) {
    dropStyle.value = {}
    return
  }
  const width = Math.min(640, window.innerWidth - 24)
  const left = Math.max(12, Math.min(r.left, window.innerWidth - width - 12))
  dropStyle.value = { left: `${left}px`, top: `${r.bottom + 6}px`, width: `${width}px` }
}

function onDocPointer(e: Event) {
  if (root.value && !root.value.contains(e.target as Node)) open.value = false
}
function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    open.value = false
    pillBtn.value?.focus()
  }
}
watch(open, (v) => {
  if (v) {
    place()
    window.addEventListener('resize', place)
    document.addEventListener('pointerdown', onDocPointer)
    document.addEventListener('keydown', onKey)
  } else {
    window.removeEventListener('resize', place)
    document.removeEventListener('pointerdown', onDocPointer)
    document.removeEventListener('keydown', onKey)
  }
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', place)
  document.removeEventListener('pointerdown', onDocPointer)
  document.removeEventListener('keydown', onKey)
})
</script>

<template>
  <div ref="root" class="activity">
    <button
      ref="pillBtn"
      type="button"
      class="pill"
      :class="{ failed: store.anyFailed, active: store.summary.spinning }"
      :aria-expanded="open"
      aria-haspopup="true"
      aria-label="Background activity"
      title="Background activity"
      data-testid="activity-pill"
      @click="open = !open"
    >
      <span class="spin" :class="{ still: !store.summary.spinning }" aria-hidden="true" />
      <span v-if="store.summary.spinning" class="count">{{ store.runningCount }}</span>
      <span v-if="store.anyFailed" class="faildot" aria-label="A process failed" />
    </button>
    <div v-if="open" class="drop" :style="dropStyle" role="region" aria-label="Background processes">
      <ActivityGrid />
    </div>
  </div>
</template>

<style scoped>
.activity { position: relative; flex: none; }
.pill {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  height: 26px;
  min-width: 34px;
  justify-content: center;
  background: var(--surface-hover);
  color: var(--text-color);
  border: 1px solid var(--surface-border);
  border-radius: 999px;
  padding: 0 11px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
}
.pill.active { border-color: var(--primary-color); }
.pill:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; }
.spin {
  width: 11px;
  height: 11px;
  border: 2px solid var(--primary-color);
  border-top-color: transparent;
  border-radius: 50%;
  display: inline-block;
  animation: act-rot 1s linear infinite;
}
.spin.still { animation: none; border-color: var(--text-color-secondary); }
.faildot { width: 8px; height: 8px; border-radius: 50%; background: var(--danger-color); }
@keyframes act-rot { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) {
  .spin { animation: none; border-top-color: var(--primary-color); }
}
.drop {
  position: fixed;
  top: 48px;
  left: 12px;
  width: 640px;
  max-width: calc(100vw - 24px);
  max-height: 70vh;
  overflow-y: auto;
  background: var(--surface-section);
  border: 1px solid var(--surface-border);
  border-radius: 8px;
  padding: 10px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.25);
  z-index: 1500;
}
@media (max-width: 767px) {
  .drop { top: auto; left: 12px; right: 12px; width: auto; max-width: none; margin-top: 6px; }
}
</style>
