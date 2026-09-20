<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import {
  listPresets,
  createPreset,
  updatePreset,
  getPreset,
  deletePreset,
  validatePreset,
  type ValidationResult,
} from '../../api/comfy'
import { ApiError } from '../../api/client'
import { VIDEO_MODES, presetTag } from '../../types/storyboard'
import type { WorkflowPreset } from '../../types/storyboard'
import TextEditPopup from './TextEditPopup.vue'

const props = withDefaults(defineProps<{ initialMode?: string }>(), {
  initialMode: 'ref2va',
})

const emit = defineEmits<{
  close: []
  registered: []
}>()

const name = ref('')
const workflowText = ref('')
// Dialect association (workflow_presets.video_target/video_mode) — drives
// target-specific validation and the generate_video mismatch guard.
// "" = untagged (legacy behavior, generic validation only).
const videoTarget = ref('minimax')
const videoMode = ref(props.initialMode)

// ---- update mode ----------------------------------------------------------
// Selecting a preset in the list below loads it into the form for an
// in-place workflow update; selecting it again returns to registering a
// new one. Only the workflow is updatable -- name and the dialect tag are
// shown (so the user can see WHAT they are updating) but locked, and the
// preset's id is preserved server-side so config slots and job history
// keep pointing at it.
const editingId = ref<number | null>(null)
// Validation must run against the preset's own kind (legacy t2i/ref
// presets can be updated too), not the 'ref2v' new registrations use.
const editingKind = ref<WorkflowPreset['kind']>('ref2v')
const loadingPreset = ref(false)
const savedNotice = ref<string | null>(null)
const isUpdating = computed(() => editingId.value !== null)

const jsonError = ref<string | null>(null)
const submitError = ref<string | null>(null)
const submitting = ref(false)

// ---- validation ----------------------------------------------------------

const validation = ref<ValidationResult | null>(null)
const validating = ref(false)
const registerWarnings = ref<string[]>([])

// Any edit to the inputs stales the last report. registerWarnings is NOT
// cleared here: submit() empties workflowText on success, and this
// (batched) watcher would wipe the just-returned warnings — they are
// cleared explicitly at the start of onValidate/submit instead.
watch([workflowText, videoTarget, videoMode], () => {
  validation.value = null
})

function parseWorkflow(): Record<string, unknown> | null {
  jsonError.value = null
  try {
    return JSON.parse(workflowText.value)
  } catch (e) {
    jsonError.value = `Invalid JSON: ${e instanceof Error ? e.message : String(e)}`
    return null
  }
}

async function onValidate(): Promise<void> {
  const workflow = parseWorkflow()
  if (!workflow) return
  submitError.value = null
  registerWarnings.value = []
  validating.value = true
  try {
    validation.value = await validatePreset({
      kind: isUpdating.value ? editingKind.value : 'ref2v',
      workflow,
      video_target: videoTarget.value || null,
      video_mode: videoMode.value || null,
    })
  } catch (e) {
    submitError.value = e instanceof ApiError ? e.message : String(e)
  } finally {
    validating.value = false
  }
}

async function onApplyFixes(): Promise<void> {
  const fixed = validation.value?.fixed_workflow
  if (!fixed) return
  workflowText.value = JSON.stringify(fixed, null, 2)
  // The watcher above just cleared the stale report — re-validate the
  // repaired graph so the user sees what remains.
  await onValidate()
}

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

// Back to the registration defaults (also leaves update mode).
function resetForm() {
  selectSeq++ // orphan any preset load still in flight
  editingId.value = null
  editingKind.value = 'ref2v'
  name.value = ''
  videoTarget.value = 'minimax'
  videoMode.value = props.initialMode
  workflowText.value = ''
  jsonError.value = null
  submitError.value = null
  validation.value = null
  registerWarnings.value = []
  savedNotice.value = null
}

let selectSeq = 0
async function onSelectPreset(p: WorkflowPreset) {
  // Re-selecting the highlighted preset toggles update mode off.
  if (editingId.value === p.id) {
    resetForm()
    return
  }
  const seq = ++selectSeq
  loadingPreset.value = true
  submitError.value = null
  try {
    const full = await getPreset(p.id)
    if (seq !== selectSeq) return // a later click won
    editingId.value = full.id
    editingKind.value = full.kind
    name.value = full.name
    videoTarget.value = full.video_target ?? ''
    videoMode.value = full.video_mode ?? ''
    workflowText.value = JSON.stringify(full.workflow, null, 2)
    jsonError.value = null
    validation.value = null
    registerWarnings.value = []
    savedNotice.value = null
  } catch (e) {
    if (seq === selectSeq) {
      submitError.value =
        e instanceof ApiError ? e.message : e instanceof Error ? e.message : String(e)
    }
  } finally {
    if (seq === selectSeq) loadingPreset.value = false
  }
}

async function submit() {
  submitError.value = null
  registerWarnings.value = []
  savedNotice.value = null
  const trimmedName = name.value.trim()
  if (!trimmedName) return

  const workflow = parseWorkflow()
  if (!workflow) return

  submitting.value = true
  try {
    if (editingId.value !== null) {
      // Update: only the workflow travels. The selection (and the form)
      // stay as they are so the user can keep iterating on the graph.
      const res = await updatePreset(editingId.value, workflow)
      registerWarnings.value = res.warnings ?? []
      savedNotice.value = `Updated “${trimmedName}”.`
      emit('registered')
      await refreshPresets()
      return
    }
    // The storyboard UX is video-only, so registration is fixed to the
    // ref2v (video workflow) kind.
    const res = await createPreset({
      name: trimmedName,
      kind: 'ref2v',
      workflow,
      video_target: videoTarget.value || null,
      video_mode: videoMode.value || null,
    })
    registerWarnings.value = res.warnings ?? []
    savedNotice.value = `Saved “${trimmedName}”.`
    name.value = ''
    workflowText.value = ''
    emit('registered')
    await refreshPresets()
  } catch (e) {
    if (
      e instanceof ApiError &&
      (e.detail as { code?: string } | undefined)?.code === 'validation_failed'
    ) {
      // Structured 400: render the findings and offer the fixes instead
      // of one opaque message.
      validation.value = e.detail as unknown as ValidationResult
      submitError.value = 'Validation failed — fix the findings below and retry.'
    } else {
      submitError.value =
        e instanceof ApiError ? e.message : e instanceof Error ? e.message : String(e)
    }
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
    // The form may still hold the preset that just went away.
    if (editingId.value === id) resetForm()
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

      <h4>Register or update a workflow</h4>
      <p v-if="isUpdating" class="update-hint">
        Updating “{{ name }}” — only the workflow can be changed. Select it
        again in the list below to go back to registering a new workflow.
      </p>
      <div class="field">
        <label for="preset-name">Name</label>
        <!-- Locked while updating: no edit popup, just the value. -->
        <InputText v-if="isUpdating" id="preset-name" :model-value="name" disabled />
        <TextEditPopup v-else title="Name" :value="name" @save="name = $event">
          <InputText id="preset-name" v-model="name" placeholder="e.g. H3 ref2v" />
        </TextEditPopup>
      </div>

      <div class="field-row">
        <div class="field">
          <label for="preset-target">Video model</label>
          <select id="preset-target" v-model="videoTarget" :disabled="isUpdating">
            <option value="">Untagged</option>
            <option value="minimax">MiniMax H3</option>
          </select>
        </div>
        <div class="field">
          <label for="preset-mode">Generation mode</label>
          <select id="preset-mode" v-model="videoMode" :disabled="isUpdating">
            <option value="">Untagged</option>
            <option v-for="m in VIDEO_MODES" :key="m" :value="m">{{ m }}</option>
          </select>
        </div>
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

      <div v-if="validation" class="validation-box">
        <p v-if="validation.findings.length === 0" class="validation-ok">
          ✓ Workflow satisfies the MS_* contract.
        </p>
        <ul v-else class="validation-list">
          <li
            v-for="(f, i) in validation.findings"
            :key="i"
            :class="f.level === 'error' ? 'v-error' : 'v-warning'"
          >
            <b>{{ f.level }}</b> · {{ f.message }}
          </li>
        </ul>
        <button
          v-if="validation.fixes.length > 0"
          type="button"
          class="btn-secondary fixes-btn"
          @click="onApplyFixes"
        >
          Apply {{ validation.fixes.length }} fix{{ validation.fixes.length === 1 ? '' : 'es' }}
        </button>
      </div>

      <p v-if="savedNotice" class="saved-notice">✓ {{ savedNotice }}</p>
      <p v-if="registerWarnings.length" class="register-warnings">
        {{ isUpdating ? 'Updated' : 'Saved' }} with {{ registerWarnings.length }} warning{{
          registerWarnings.length === 1 ? '' : 's'
        }}: {{ registerWarnings.join(' ') }}
      </p>

      <div class="dialog-actions">
        <button
          class="btn-primary"
          :disabled="!name.trim() || !workflowText.trim() || submitting"
          @click="submit"
        >
          <template v-if="isUpdating">{{ submitting ? 'Updating…' : 'Update' }}</template>
          <template v-else>{{ submitting ? 'Saving…' : 'Save' }}</template>
        </button>
        <button
          class="btn-secondary"
          :disabled="!workflowText.trim() || validating"
          @click="onValidate"
        >
          {{ validating ? 'Validating…' : 'Validate' }}
        </button>
        <button class="btn-secondary" @click="close">Close</button>
      </div>

      <h4 class="presets-heading">Existing presets</h4>
      <div v-if="presetsLoading" class="muted">Loading…</div>
      <div v-else-if="presets.length === 0" class="muted">No presets registered yet.</div>
      <div v-else class="preset-list">
        <div
          v-for="p in presets"
          :key="p.id"
          class="preset-row"
          :class="{ selected: editingId === p.id }"
        >
          <button
            type="button"
            class="preset-info"
            :aria-pressed="editingId === p.id"
            :disabled="loadingPreset || submitting"
            :title="editingId === p.id
              ? 'Being updated — select again to go back to a new workflow'
              : 'Select to update this workflow'"
            @click="onSelectPreset(p)"
          >
            <div class="preset-name">
              {{ p.name }}
              <span v-if="editingId === p.id" class="updating-chip">updating</span>
            </div>
            <div class="preset-meta">{{ p.kind }}{{ presetTag(p) }}</div>
          </button>
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

.field-row {
  display: flex;
  gap: 12px;
}

.field-row .field {
  flex: 1;
}

select {
  padding: 6px 10px;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-card);
  color: var(--text-color);
  font-size: 13px;
  font-family: inherit;
  width: 100%;
  box-sizing: border-box;
}

select:focus {
  outline: none;
  border-color: var(--primary-color);
}

.validation-box {
  margin: 10px 0 0;
  padding: 10px 12px;
  border: 1px solid var(--surface-border);
  border-radius: 8px;
  background: var(--surface-ground);
}

.validation-ok {
  margin: 0;
  font-size: 13px;
  color: var(--ok, #4caf50);
}

.validation-list {
  margin: 0;
  padding-left: 16px;
  font-size: 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.v-error {
  color: var(--danger-color, #e53e3e);
}

.v-warning {
  color: var(--warn, #ffb300);
}

.fixes-btn {
  margin-top: 10px;
}

.register-warnings {
  margin: 10px 0 0;
  font-size: 12px;
  color: var(--warn, #ffb300);
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

/* The selected (being-updated) row stays highlighted for as long as
   update mode lasts: tinted background plus a primary-colored left bar. */
.preset-row.selected {
  background: color-mix(in srgb, var(--primary-color) 14%, transparent);
  box-shadow: inset 3px 0 0 var(--primary-color);
}

.preset-info {
  flex: 1;
  min-width: 0;
  text-align: left;
  border: none;
  background: none;
  padding: 0;
  font: inherit;
  cursor: pointer;
}
.preset-info:disabled { cursor: default; }
.preset-row:not(.selected):hover {
  background: var(--surface-hover, rgba(127, 127, 127, 0.12));
}

.updating-chip {
  margin-left: 6px;
  padding: 1px 6px;
  border-radius: 8px;
  font-size: 10px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--primary-color-text, #fff);
  background: var(--primary-color);
}

.update-hint {
  margin: 0 0 10px;
  font-size: 12px;
  color: var(--text-color-secondary);
}

.saved-notice {
  margin: 10px 0 0;
  font-size: 12px;
  color: var(--green-500, #2e9d5b);
}

select:disabled {
  opacity: 0.6;
  cursor: default;
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
