<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { listPresets, createPreset, deletePreset } from '../../api/comfy'
import { ApiError } from '../../api/client'
import type { WorkflowPreset } from '../../types/storyboard'
import TextEditPopup from './TextEditPopup.vue'

const emit = defineEmits<{
  close: []
  registered: []
}>()

const name = ref('')
const workflowText = ref('')

const jsonError = ref<string | null>(null)
const submitError = ref<string | null>(null)
const submitting = ref(false)

const presets = ref<WorkflowPreset[]>([])
const presetsLoading = ref(true)
const deleteError = ref<string | null>(null)

onMounted(refreshPresets)

async function refreshPresets() {
  presetsLoading.value = true
  try {
    presets.value = await listPresets()
  } catch (e) {
    deleteError.value = e instanceof Error ? e.message : String(e)
  } finally {
    presetsLoading.value = false
  }
}

async function onFileChange(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  workflowText.value = await file.text()
  input.value = ''
}

async function submit() {
  jsonError.value = null
  submitError.value = null
  const trimmedName = name.value.trim()
  if (!trimmedName) return

  let workflow: Record<string, unknown>
  try {
    workflow = JSON.parse(workflowText.value)
  } catch (e) {
    jsonError.value = `Invalid JSON: ${e instanceof Error ? e.message : String(e)}`
    return
  }

  submitting.value = true
  try {
    // The storyboard UX is video-only, so registration is fixed to the
    // ref2v (video workflow) kind.
    await createPreset({ name: trimmedName, kind: 'ref2v', workflow })
    name.value = ''
    workflowText.value = ''
    emit('registered')
    await refreshPresets()
  } catch (e) {
    submitError.value = e instanceof ApiError ? e.message : e instanceof Error ? e.message : String(e)
  } finally {
    submitting.value = false
  }
}

async function onDelete(id: number, presetName: string) {
  deleteError.value = null
  if (!confirm(`Delete preset "${presetName}"?`)) return
  try {
    await deletePreset(id)
    presets.value = presets.value.filter((p) => p.id !== id)
  } catch (e) {
    // A 409 from the backend names the jobs still referencing this preset --
    // surfaced verbatim, same treatment as the JSON-validation error above.
    deleteError.value = e instanceof ApiError ? e.message : e instanceof Error ? e.message : String(e)
  }
}

function close() {
  emit('close')
}
</script>

<template>
  <div class="dialog-overlay" @click.self="close">
    <div class="dialog-card">
      <h3>Workflow presets</h3>

      <h4>Register a new video preset (ref2v)</h4>
      <div class="field">
        <label for="preset-name">Name</label>
        <TextEditPopup title="Name" :value="name" @save="name = $event">
          <InputText id="preset-name" v-model="name" placeholder="e.g. H3 ref2v" />
        </TextEditPopup>
      </div>

      <div class="field">
        <label for="preset-workflow">Workflow JSON</label>
        <input type="file" accept=".json" @change="onFileChange" />
        <textarea
          id="preset-workflow"
          v-model="workflowText"
          rows="8"
          class="workflow-text"
          placeholder="Paste an API-format ComfyUI workflow, or load a file above"
        />
      </div>

      <p v-if="jsonError" class="error">{{ jsonError }}</p>
      <p v-if="submitError" class="error">{{ submitError }}</p>

      <div class="dialog-actions">
        <button
          class="btn-primary"
          :disabled="!name.trim() || !workflowText.trim() || submitting"
          @click="submit"
        >
          {{ submitting ? 'Registering…' : 'Register' }}
        </button>
        <button class="btn-secondary" @click="close">Close</button>
      </div>

      <h4 class="presets-heading">Existing presets</h4>
      <div v-if="presetsLoading" class="muted">Loading…</div>
      <div v-else-if="presets.length === 0" class="muted">No presets registered yet.</div>
      <div v-else class="preset-list">
        <div v-for="p in presets" :key="p.id" class="preset-row">
          <div class="preset-info">
            <div class="preset-name">{{ p.name }}</div>
            <div class="preset-meta">{{ p.kind }}</div>
          </div>
          <button class="btn-remove" title="Delete" @click="onDelete(p.id, p.name)">
            &times;
          </button>
        </div>
      </div>
      <p v-if="deleteError" class="error">{{ deleteError }}</p>
    </div>
  </div>
</template>

<style scoped>
.dialog-overlay {
  position: fixed;
  inset: 0;
  z-index: 950;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
}

.dialog-card {
  background: var(--surface-section);
  border-radius: 12px;
  padding: 22px 28px 24px;
  width: 560px;
  max-width: 92vw;
  max-height: 85vh;
  overflow-y: auto;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
}

h3 {
  margin: 0 0 16px;
  font-size: 18px;
  color: var(--text-color);
}

h4 {
  margin: 0 0 10px;
  font-size: 14px;
  color: var(--text-color);
}

.presets-heading {
  margin-top: 22px;
  padding-top: 16px;
  border-top: 1px solid var(--surface-border);
}

.field {
  margin-bottom: 14px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

label {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-color-secondary);
}

input[type='file'] {
  font-size: 12px;
  color: var(--text-color-secondary);
}

.workflow-text {
  margin-top: 6px;
  font-family: ui-monospace, monospace;
  font-size: 12px;
}

textarea,
input[type='text'] {
  padding: 6px 10px;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-card);
  color: var(--text-color);
  font-size: 13px;
  font-family: inherit;
  width: 100%;
  box-sizing: border-box;
  resize: vertical;
}

textarea:focus {
  outline: none;
  border-color: var(--primary-color);
}

.error {
  color: var(--danger-color, #e53e3e);
  font-size: 13px;
  margin: 4px 0 0;
  white-space: pre-wrap;
}

.muted {
  color: var(--text-color-secondary);
  font-size: 13px;
  padding: 8px 0;
}

.dialog-actions {
  display: flex;
  gap: 10px;
  margin-top: 12px;
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
.btn-secondary:hover {
  background: var(--surface-hover);
}

.preset-list {
  border: 1px solid var(--surface-border);
  border-radius: 8px;
  overflow: hidden;
}

.preset-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--surface-border);
}
.preset-row:last-child {
  border-bottom: none;
}

.preset-name {
  font-size: 13px;
  color: var(--text-color);
}

.preset-meta {
  font-size: 12px;
  color: var(--text-color-secondary);
}

.btn-remove {
  background: none;
  border: none;
  color: var(--text-color-secondary);
  font-size: 18px;
  cursor: pointer;
  padding: 0 4px;
  line-height: 1;
  flex-shrink: 0;
}
.btn-remove:hover {
  color: var(--danger-color, #e53e3e);
}
</style>
