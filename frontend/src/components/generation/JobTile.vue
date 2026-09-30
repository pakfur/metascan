<script setup lang="ts">
import { computed } from 'vue'
import { jobChipLabel, type JobChip } from '../../types/jobs'

// A dashed placeholder tile for a job that has not produced its file yet:
// queued / running with progress, or failed with the server's error text
// shown verbatim. The ✕ cancels a live job and dismisses a failed one.
const props = defineProps<{ chip: JobChip }>()
const emit = defineEmits<{ cancel: []; dismiss: [] }>()

const failed = computed(() => props.chip.state === 'failed')
const label = computed(() => jobChipLabel(props.chip))

function onClose() {
  if (failed.value) emit('dismiss')
  else emit('cancel')
}
</script>

<template>
  <div
    class="job-tile"
    :class="{ failed }"
    :title="failed ? (chip.error ?? undefined) : undefined"
  >
    <button
      type="button"
      class="job-x"
      :title="failed ? 'Dismiss' : 'Cancel this job'"
      :disabled="!failed && !!chip.cancelling"
      @click="onClose"
    >✕</button>
    <span class="job-label">{{ label }}</span>
    <span v-if="failed && chip.error" class="job-error">{{ chip.error }}</span>
  </div>
</template>

<style scoped>
.job-tile {
  position: relative;
  flex: 0 0 var(--tile-size, 132px);
  width: var(--tile-size, 132px);
  height: var(--tile-size, 132px);
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 18px 8px 8px;
  border: 1px dashed #666;
  border-radius: 6px;
  font-size: 13px;
  color: var(--text-color-secondary);
}

.job-tile.failed {
  border-color: #c33;
  color: #c33;
}

.job-x {
  position: absolute;
  top: 2px;
  right: 2px;
  border: none;
  background: none;
  color: inherit;
  cursor: pointer;
  font-size: 13px;
  padding: 4px;
}

.job-x:hover:not(:disabled) {
  color: var(--text-color);
}

.job-x:disabled {
  opacity: 0.5;
  cursor: default;
}

.job-error {
  max-width: 100%;
  font-size: 11px;
  line-height: 1.3;
  text-align: center;
  word-break: break-word;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 4;
  line-clamp: 4;
  overflow: hidden;
}
</style>
