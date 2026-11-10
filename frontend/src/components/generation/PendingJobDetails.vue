<script setup lang="ts">
// What a queued / running job was submitted with, read-only: shown in place
// of the form while its placeholder tile is selected, so nothing here can
// change the form or the job. Every value sits in a read-only box, so it can
// be selected and copied but not edited; multi-line text keeps its line
// breaks. The caller builds the rows (labels and values are its own words).
import type { PendingJobRow } from '../../types/jobs'

defineProps<{
  // "Queued video · running 42%"
  status: string
  rows: PendingJobRow[]
  loading?: boolean
  error?: string | null
}>()
const emit = defineEmits<{ close: [] }>()

function lineCount(text: string): number {
  return Math.min(16, Math.max(3, text.split('\n').length + Math.ceil(text.length / 90)))
}
</script>

<template>
  <section class="pjd" aria-label="Queued job details">
    <div class="pjd-bar" role="status">
      <span>{{ status }} · read-only</span>
      <button
        type="button"
        class="pjd-link"
        title="Back to the form (it is just as you left it)"
        @click="emit('close')"
      >Back to the form</button>
    </div>
    <p v-if="loading" class="pjd-note">Loading…</p>
    <p v-else-if="error" class="pjd-note warn" role="alert">{{ error }}</p>
    <template v-else>
      <div v-for="row in rows" :key="row.label" class="pjd-fld">
        <span class="pjd-label">{{ row.label }}</span>
        <textarea
          v-if="row.multiline"
          class="pjd-box"
          readonly
          spellcheck="false"
          :rows="lineCount(row.value ?? '')"
          :value="row.value || '—'"
          :aria-label="row.label"
        />
        <input
          v-else
          class="pjd-box"
          readonly
          spellcheck="false"
          :value="row.value || '—'"
          :aria-label="row.label"
        />
      </div>
    </template>
  </section>
</template>

<style scoped>
.pjd {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.pjd-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 6px 10px;
  border-radius: 6px;
  font-size: 12px;
  color: var(--text-color-secondary);
  background: var(--surface-ground);
  border: 1px solid var(--surface-border);
  border-left: 3px dashed var(--primary-color, #6366f1);
}

.pjd-link {
  background: none;
  border: none;
  padding: 0;
  cursor: pointer;
  font-size: 12px;
  color: var(--primary-color, #6366f1);
  white-space: nowrap;
}

.pjd-link:hover {
  text-decoration: underline;
}

.pjd-fld {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.pjd-label {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.4px;
  color: var(--text-color-secondary);
}

.pjd-box {
  width: 100%;
  box-sizing: border-box;
  font-family: inherit;
  font-size: 13px;
  line-height: 1.4;
  color: var(--text-color);
  background: var(--surface-ground);
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  padding: 6px 8px;
  white-space: pre-wrap;
  resize: vertical;
}

input.pjd-box {
  resize: none;
}

.pjd-box:focus {
  outline: none;
  border-color: var(--primary-color);
}

.pjd-note {
  margin: 0;
  font-size: 12px;
  color: var(--text-color-secondary);
}

.pjd-note.warn {
  color: #c33;
}
</style>
