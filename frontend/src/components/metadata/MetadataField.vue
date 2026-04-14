<script setup lang="ts">
import { ref } from 'vue'

defineProps<{
  label: string
  value: string
  multiline?: boolean
}>()

const copied = ref(false)

function copy(text: string) {
  navigator.clipboard.writeText(text)
  copied.value = true
  setTimeout(() => (copied.value = false), 1200)
}
</script>

<template>
  <div class="meta-field" :class="{ multiline }">
    <div class="field-header">
      <span class="field-label">{{ label }}</span>
      <button
        class="copy-btn"
        :class="{ copied }"
        @click="copy(value)"
        :title="copied ? 'Copied!' : 'Copy'"
      >
        {{ copied ? '✓' : '📋' }}
      </button>
    </div>
    <div v-if="multiline" class="field-value-multi">{{ value }}</div>
    <div v-else class="field-value">{{ value }}</div>
  </div>
</template>

<style scoped>
.meta-field {
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.field-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.field-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-color-secondary);
  text-transform: uppercase;
  letter-spacing: 0.03em;
}

.copy-btn {
  background: none;
  border: none;
  cursor: pointer;
  font-size: 12px;
  padding: 0 2px;
  opacity: 0;
  transition: opacity 0.15s;
}

.meta-field:hover .copy-btn {
  opacity: 0.6;
}

.copy-btn:hover {
  opacity: 1 !important;
}

.copy-btn.copied {
  opacity: 1;
  color: #22c55e;
}

.field-value {
  font-size: 12px;
  color: var(--text-color);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.field-value-multi {
  font-size: 12px;
  color: var(--text-color);
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 120px;
  overflow-y: auto;
  line-height: 1.4;
}
</style>
