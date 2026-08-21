<script setup lang="ts">
import { ref } from 'vue'
import { useStoryboardStore } from '../../stores/storyboard'
import { ApiError } from '../../api/client'

const emit = defineEmits<{ close: [] }>()
const store = useStoryboardStore()

const text = ref('')
const busy = ref(false)
const errorMsg = ref<string | null>(null)
const confirmRequired = ref(false)
const confirmMessage = ref('')

async function doImport(confirm: boolean): Promise<void> {
  if (busy.value) return
  busy.value = true
  errorMsg.value = null
  confirmRequired.value = false
  try {
    await store.importText(text.value, confirm)
    emit('close')
  } catch (e) {
    if (e instanceof ApiError && e.status === 409) {
      confirmRequired.value = true
      confirmMessage.value =
        'This storyboard already has scenes. Re-parsing replaces all scenes, panels and hand-edited prompts, and deletes their generated images and clips (moved to the OS trash).'
    } else if (e instanceof ApiError && e.status === 422) {
      errorMsg.value = `The model couldn't parse this text: ${e.message}`
    } else if (e instanceof ApiError && e.status === 503) {
      errorMsg.value = e.message
    } else {
      errorMsg.value = e instanceof Error ? e.message : String(e)
    }
  } finally {
    busy.value = false
  }
}

function close(): void {
  if (busy.value) return
  emit('close')
}
</script>

<template>
  <div class="dialog-overlay" @click.self="close">
    <div class="dialog-card">
      <h3>Import text</h3>
      <p class="hint">
        Paste your scene text — subjects, locations, one or two sentences per shot.
      </p>

      <textarea
        v-model="text"
        rows="14"
        class="import-textarea"
        placeholder="Paste scene text here…"
        :disabled="busy"
      />

      <p v-if="confirmRequired" class="warn">{{ confirmMessage }}</p>
      <p v-else-if="errorMsg" class="error">{{ errorMsg }}</p>
      <p v-if="busy" class="busy-line">
        <span class="pi pi-spin pi-spinner" /> Parsing… this can take a minute.
      </p>

      <div class="dialog-actions">
        <template v-if="confirmRequired">
          <button class="btn-danger" :disabled="busy" @click="doImport(true)">
            Replace structure
          </button>
          <button class="btn-secondary" :disabled="busy" @click="confirmRequired = false">
            Cancel
          </button>
        </template>
        <template v-else>
          <button class="btn-primary" :disabled="busy || !text.trim()" @click="doImport(false)">
            {{ busy ? 'Importing…' : 'Import' }}
          </button>
          <button class="btn-secondary" :disabled="busy" @click="close">Cancel</button>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
.dialog-overlay {
  position: fixed;
  inset: 0;
  z-index: 900;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
}

.dialog-card {
  background: var(--surface-section);
  border-radius: 12px;
  padding: 22px 28px 24px;
  width: 640px;
  max-width: 92vw;
  max-height: 85vh;
  overflow-y: auto;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
}

h3 {
  margin: 0 0 8px;
  font-size: 18px;
  color: var(--text-color);
}

.hint {
  margin: 0 0 14px;
  font-size: 12px;
  color: var(--text-color-secondary);
}

.import-textarea {
  width: 100%;
  box-sizing: border-box;
  resize: vertical;
  padding: 10px;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-card);
  color: var(--text-color);
  font-size: 13px;
  font-family: inherit;
}

.import-textarea:focus {
  outline: none;
  border-color: var(--primary-color);
}

.import-textarea:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.warn {
  margin: 10px 0 0;
  padding: 8px 10px;
  border-radius: 6px;
  font-size: 13px;
  color: var(--text-color);
  background: color-mix(in srgb, var(--danger-color, #e53e3e) 12%, transparent);
  border: 1px solid color-mix(in srgb, var(--danger-color, #e53e3e) 40%, transparent);
}

.error {
  color: var(--danger-color, #e53e3e);
  font-size: 13px;
  margin: 10px 0 0;
}

.busy-line {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 10px 0 0;
  font-size: 12px;
  color: var(--text-color-secondary);
}

.dialog-actions {
  display: flex;
  gap: 10px;
  margin-top: 20px;
}

.btn-primary {
  padding: 8px 20px;
  background: var(--primary-color);
  border: none;
  border-radius: 6px;
  color: #fff;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
}
.btn-primary:hover:not(:disabled) {
  opacity: 0.9;
}
.btn-primary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.btn-secondary {
  padding: 8px 20px;
  background: var(--surface-ground);
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  color: var(--text-color);
  font-size: 14px;
  cursor: pointer;
}
.btn-secondary:hover:not(:disabled) {
  background: var(--surface-hover);
}
.btn-secondary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.btn-danger {
  padding: 8px 20px;
  background: var(--danger-color, #e53e3e);
  border: none;
  border-radius: 6px;
  color: #fff;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
}
.btn-danger:hover:not(:disabled) {
  opacity: 0.9;
}
.btn-danger:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
