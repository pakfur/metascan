<script setup lang="ts">
import { ref, watch } from 'vue'
import { useSimilarityStore } from '../../stores/similarity'

const simStore = useSimilarityStore()
const localThreshold = ref(simStore.threshold)
let debounce: ReturnType<typeof setTimeout> | null = null

watch(() => simStore.threshold, (v) => { localThreshold.value = v })

function onThresholdInput(val: number) {
  localThreshold.value = val
  if (debounce) clearTimeout(debounce)
  debounce = setTimeout(() => {
    simStore.updateThreshold(val)
  }, 300)
}
</script>

<template>
  <div class="similarity-banner">
    <div class="banner-left">
      <span v-if="simStore.isContentSearch" class="banner-label">
        Content search: <strong>"{{ simStore.contentQuery }}"</strong>
      </span>
      <span v-else-if="simStore.referenceMedia" class="banner-label">
        Similar to: <strong>{{ simStore.referenceMedia.file_name }}</strong>
      </span>
      <span class="result-count">
        {{ simStore.filteredResults.length }} results
        <span v-if="simStore.loading" class="loading-dot">...</span>
      </span>
    </div>

    <div class="banner-center">
      <label class="threshold-label">Threshold</label>
      <input
        type="range"
        class="threshold-slider"
        min="0"
        max="1"
        step="0.05"
        :value="localThreshold"
        @input="onThresholdInput(parseFloat(($event.target as HTMLInputElement).value))"
      />
      <span class="threshold-value">{{ (localThreshold * 100).toFixed(0) }}%</span>
    </div>

    <button class="exit-btn" @click="simStore.exit()" title="Exit similarity mode (Esc)">
      Exit
    </button>
  </div>
</template>

<style scoped>
.similarity-banner {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 8px 16px;
  background: color-mix(in srgb, var(--primary-color) 12%, var(--surface-section));
  border-bottom: 2px solid var(--primary-color);
  flex-shrink: 0;
}

.banner-left {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.banner-label {
  font-size: 13px;
  color: var(--text-color);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.result-count {
  font-size: 12px;
  color: var(--text-color-secondary);
}

.loading-dot {
  animation: blink 1s infinite;
}

@keyframes blink {
  50% { opacity: 0; }
}

.banner-center {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  justify-content: center;
}

.threshold-label {
  font-size: 12px;
  color: var(--text-color-secondary);
  white-space: nowrap;
}

.threshold-slider {
  width: 160px;
  accent-color: var(--primary-color);
}

.threshold-value {
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  color: var(--text-color);
  min-width: 36px;
}

.exit-btn {
  padding: 4px 14px;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-card);
  color: var(--text-color);
  font-size: 13px;
  cursor: pointer;
  white-space: nowrap;
}

.exit-btn:hover {
  background: var(--surface-hover);
}
</style>
