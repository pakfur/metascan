<script setup lang="ts">
import type { JobState } from '../../types/storyboard'

// Fill-layout job overlay (spec "Job states"): absolute inset-0 over a
// thumbnail. queued -> hourglass glyph, running -> PrimeIcons spinner,
// failed -> warning glyph with the error in title. No badge for done.
defineProps<{ state: JobState; error?: string | null }>()
</script>

<template>
  <span
    class="jb"
    :class="{ failed: state === 'failed' }"
    :title="state === 'failed' ? (error ?? 'failed') : undefined"
  >
    <span v-if="state === 'queued'">⏳</span>
    <span v-else-if="state === 'running'" class="pi pi-spin pi-spinner" />
    <span v-else-if="state === 'failed'">⚠</span>
  </span>
</template>

<style scoped>
.jb {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
}

.jb.failed {
  background: color-mix(in srgb, var(--danger-color) 70%, black);
  cursor: help;
}
</style>
