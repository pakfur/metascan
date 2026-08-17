<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useStoryboardStore } from '../../stores/storyboard'
import { listPresets } from '../../api/comfy'
import { describeSubject } from '../../api/storyboard'
import { ApiError, thumbnailUrl } from '../../api/client'
import ReferenceImagePicker from './ReferenceImagePicker.vue'
import { ASPECT_RATIOS, TARGET_MODELS, VIDEO_TARGETS, VIDEO_MODES } from '../../types/storyboard'
import type { WorkflowPreset } from '../../types/storyboard'

const emit = defineEmits<{ close: [] }>()
const store = useStoryboardStore()

// ---- storyboard fields (commit as a batch on Save) ----------------------

const name = ref('')
const aspectRatio = ref('')
const targetModel = ref('')
const presetId = ref<number | null>(null)
const batchSize = ref(1)
const baseSeed = ref(0)
const styleBlock = ref('')
const negative = ref('')
const notes = ref('')
const videoTarget = ref<string | null>(null)
const videoMode = ref<string | null>(null)
const videoPresetId = ref<number | null>(null)

const presets = ref<WorkflowPreset[]>([])
const presetsLoading = ref(true)
const saving = ref(false)
const fieldsError = ref<string | null>(null)

// Snapshot of the last-saved (or last-loaded) values, used to build a
// changed-fields-only PATCH body. Deliberately a plain object (not a
// ref) -- it's write-only bookkeeping, never rendered.
let original = {
  name: '',
  aspectRatio: '',
  targetModel: '',
  presetId: null as number | null,
  batchSize: 1,
  baseSeed: 0,
  styleBlock: '',
  negativeVal: '',
  notesVal: '',
  videoTarget: null as string | null,
  videoMode: null as string | null,
  videoPresetId: null as number | null,
}

function seedFromTree(): void {
  const t = store.tree
  if (!t) return
  name.value = t.name
  aspectRatio.value = t.aspect_ratio
  targetModel.value = t.target_model
  presetId.value = t.preset_id
  batchSize.value = t.batch_size
  baseSeed.value = t.base_seed
  styleBlock.value = t.style_block ?? ''
  negative.value = t.negative ?? ''
  notes.value = t.notes ?? ''
  videoTarget.value = t.video_target
  videoMode.value = t.video_mode
  videoPresetId.value = t.video_preset_id
  original = {
    name: name.value,
    aspectRatio: aspectRatio.value,
    targetModel: targetModel.value,
    presetId: presetId.value,
    batchSize: batchSize.value,
    baseSeed: baseSeed.value,
    styleBlock: styleBlock.value,
    negativeVal: negative.value,
    notesVal: notes.value,
    videoTarget: videoTarget.value,
    videoMode: videoMode.value,
    videoPresetId: videoPresetId.value,
  }
}

const ref2vPresets = computed(() => presets.value.filter((p) => p.kind === 'ref2v'))

onMounted(async () => {
  seedFromTree()
  presetsLoading.value = true
  try {
    presets.value = await listPresets()
  } catch {
    // Non-fatal: the preset select just falls back to "None" + existing id.
    presets.value = []
  } finally {
    presetsLoading.value = false
  }
})

function clampBatchSize(): void {
  const n = Math.round(batchSize.value)
  batchSize.value = Number.isFinite(n) ? Math.min(16, Math.max(1, n)) : 1
}

async function saveFields(): Promise<void> {
  if (!store.tree) return
  clampBatchSize()
  fieldsError.value = null

  const body: Partial<{
    name: string
    aspect_ratio: string
    target_model: string
    preset_id: number | null
    batch_size: number
    base_seed: number
    style_block: string
    negative: string
    notes: string | null
    video_target: string | null
    video_mode: string | null
    video_preset_id: number | null
  }> = {}

  const trimmedName = name.value.trim()
  if (trimmedName !== original.name) body.name = trimmedName
  if (aspectRatio.value !== original.aspectRatio) body.aspect_ratio = aspectRatio.value
  if (targetModel.value !== original.targetModel) body.target_model = targetModel.value
  // Selecting "None" sends an explicit `preset_id: null` clear -- the
  // backend now honors that (exclude_unset=True) instead of silently
  // dropping it, so this must count as a real changed field too, or
  // "None + nothing else changed" would hit the empty-body early return
  // below and never save.
  if (presetId.value !== original.presetId) {
    body.preset_id = presetId.value
  }
  if (batchSize.value !== original.batchSize) body.batch_size = batchSize.value
  if (baseSeed.value !== original.baseSeed) body.base_seed = baseSeed.value
  if (styleBlock.value !== original.styleBlock) body.style_block = styleBlock.value
  if (negative.value !== original.negativeVal) body.negative = negative.value
  if (notes.value !== original.notesVal) body.notes = notes.value
  // Selecting "None" sends an explicit clear -- same null-diff handling as
  // preset_id above (the backend's exclude_unset=True honors it).
  if (videoTarget.value !== original.videoTarget) body.video_target = videoTarget.value
  if (videoMode.value !== original.videoMode) body.video_mode = videoMode.value
  // Same "None" -> explicit-clear diff handling as preset_id above.
  if (videoPresetId.value !== original.videoPresetId) {
    body.video_preset_id = videoPresetId.value
  }

  if (Object.keys(body).length === 0) return

  saving.value = true
  store.error = null
  try {
    await store.patchStoryboardFields(body)
    if (store.error) {
      fieldsError.value = store.error
      store.error = null
    } else {
      // patchStoryboardFields merges the saved fields onto store.tree in
      // place -- re-seed from it so `original` tracks what's now persisted.
      seedFromTree()
    }
  } finally {
    saving.value = false
  }
}

// ---- subjects (commit per-field on change) -------------------------------

const subjectErrors = ref<Record<number, string | null>>({})

function describeError(e: unknown): string {
  return e instanceof ApiError ? e.message : e instanceof Error ? e.message : String(e)
}

async function commitSubjectField(id: number, body: Record<string, unknown>): Promise<void> {
  subjectErrors.value[id] = null
  try {
    await store.patchSubjectFields(id, body)
  } catch (e) {
    subjectErrors.value[id] = describeError(e)
  }
}

function onSubjectName(id: number, e: Event): void {
  void commitSubjectField(id, { name: (e.target as HTMLInputElement).value })
}

function onSubjectDescription(id: number, e: Event): void {
  void commitSubjectField(id, { description: (e.target as HTMLInputElement).value })
}

function onSubjectLoraName(id: number, e: Event): void {
  const val = (e.target as HTMLInputElement).value.trim()
  void commitSubjectField(id, { lora_name: val || null })
}

function onSubjectLoraStrength(id: number, e: Event): void {
  const raw = (e.target as HTMLInputElement).value
  void commitSubjectField(id, { lora_strength: raw === '' ? null : Number(raw) })
}

function onSubjectVoice(id: number, e: Event): void {
  const val = (e.target as HTMLInputElement).value.trim()
  void commitSubjectField(id, { voice: val || null })
}

function onSubjectVoiceRefPath(id: number, e: Event): void {
  const val = (e.target as HTMLInputElement).value.trim()
  void commitSubjectField(id, { voice_ref_path: val || null })
}

function referenceField(slot: 1 | 2): 'reference_path' | 'reference_path_2' {
  return slot === 1 ? 'reference_path' : 'reference_path_2'
}

function onSubjectReferencePath(id: number, e: Event, slot: 1 | 2 = 1): void {
  const val = (e.target as HTMLInputElement).value.trim()
  void commitSubjectField(id, { [referenceField(slot)]: val || null })
}

// ---- reference image picker ---------------------------------------------

// Subject + slot the picker is currently open for, or null when closed.
// A single picker instance serves both reference rows.
const picker = ref<{ id: number; slot: 1 | 2 } | null>(null)

// Per-subject record of the reference_path whose thumbnail failed to load.
// Keyed by the path itself so a subsequent path change retries naturally.
// Slot 1 and slot 2 track separately since a subject can have both set.
const refThumbFailed = ref<Record<number, string>>({})
const refThumbFailed2 = ref<Record<number, string>>({})

function onRefThumbError(id: number, path: string, slot: 1 | 2 = 1): void {
  if (slot === 1) refThumbFailed.value[id] = path
  else refThumbFailed2.value[id] = path
}

function onPickReference(path: string): void {
  const target = picker.value
  picker.value = null
  if (target != null) void commitSubjectField(target.id, { [referenceField(target.slot)]: path })
}

function onClearReference(id: number, slot: 1 | 2 = 1): void {
  void commitSubjectField(id, { [referenceField(slot)]: null })
}

// ---- describe from refs --------------------------------------------------

const describing = ref<Record<number, boolean>>({})
const describeResult = ref<Record<number, { description: string; voice: string | null }>>({})
const describeErrors = ref<Record<number, string>>({})

async function onDescribeSubject(id: number): Promise<void> {
  describing.value[id] = true
  delete describeErrors.value[id]
  try {
    describeResult.value[id] = await describeSubject(id)
  } catch (e) {
    describeErrors.value[id] = describeError(e)
  } finally {
    describing.value[id] = false
  }
}

async function onApplyDescribe(id: number): Promise<void> {
  const result = describeResult.value[id]
  if (!result) return
  const subject = store.tree?.subjects.find((s) => s.id === id)
  const body: Record<string, unknown> = { description: result.description }
  if (result.voice && !(subject?.voice ?? '').trim()) {
    body.voice = result.voice
  }
  await commitSubjectField(id, body)
  delete describeResult.value[id]
}

function onDismissDescribe(id: number): void {
  delete describeResult.value[id]
}

async function onDeleteSubject(id: number): Promise<void> {
  if (!confirm('Delete this subject?')) return
  try {
    await store.removeSubject(id)
    delete subjectErrors.value[id]
  } catch (e) {
    subjectErrors.value[id] = describeError(e)
  }
}

const newSubjectName = ref('')
const newSubjectDescription = ref('')
const addSubjectError = ref<string | null>(null)

async function onAddSubject(): Promise<void> {
  const nm = newSubjectName.value.trim()
  if (!nm) return
  addSubjectError.value = null
  try {
    await store.addSubject({ name: nm, description: newSubjectDescription.value.trim() })
    newSubjectName.value = ''
    newSubjectDescription.value = ''
  } catch (e) {
    addSubjectError.value = describeError(e)
  }
}

function close(): void {
  emit('close')
}
</script>

<template>
  <div class="dialog-overlay" @click.self="close">
    <div class="dialog-card">
      <h3>Storyboard settings</h3>

      <section class="section">
        <h4>Fields</h4>

        <div class="field">
          <label for="ss-name">Name</label>
          <InputText id="ss-name" v-model="name" />
        </div>

        <div class="field-row">
          <div class="field">
            <label for="ss-ar">Aspect ratio</label>
            <select id="ss-ar" v-model="aspectRatio">
              <option v-for="ar in ASPECT_RATIOS" :key="ar" :value="ar">{{ ar }}</option>
            </select>
          </div>
          <div class="field">
            <label for="ss-model">Target model</label>
            <select id="ss-model" v-model="targetModel">
              <option v-for="m in TARGET_MODELS" :key="m" :value="m">{{ m }}</option>
            </select>
          </div>
        </div>

        <div class="field">
          <label for="ss-preset">Workflow preset</label>
          <select id="ss-preset" v-model="presetId" :disabled="presetsLoading">
            <option :value="null">None</option>
            <option v-for="p in presets" :key="p.id" :value="p.id">
              {{ p.name }} ({{ p.kind }})
            </option>
          </select>
        </div>

        <div class="field-row">
          <div class="field">
            <label for="ss-batch">Batch size</label>
            <input
              id="ss-batch"
              v-model.number="batchSize"
              type="number"
              min="1"
              max="16"
              @blur="clampBatchSize"
            />
          </div>
          <div class="field">
            <label for="ss-seed">Base seed</label>
            <input id="ss-seed" v-model.number="baseSeed" type="number" />
          </div>
        </div>

        <div class="field">
          <label for="ss-style">Style block</label>
          <textarea id="ss-style" v-model="styleBlock" rows="3" />
        </div>

        <div class="field">
          <label for="ss-negative">Negative</label>
          <textarea id="ss-negative" v-model="negative" rows="2" />
        </div>

        <div class="field">
          <label for="ss-notes">Notes</label>
          <textarea id="ss-notes" v-model="notes" rows="2" />
        </div>

        <div class="field-row">
          <div class="field">
            <label for="ss-video-target">Video target</label>
            <select id="ss-video-target" v-model="videoTarget">
              <option :value="null">None</option>
              <option v-for="t in VIDEO_TARGETS" :key="t" :value="t">MiniMax H3</option>
            </select>
          </div>
          <div class="field">
            <label for="ss-video-mode">Video mode</label>
            <select id="ss-video-mode" v-model="videoMode">
              <option :value="null">None</option>
              <option v-for="m in VIDEO_MODES" :key="m" :value="m">{{ m.toUpperCase() }}</option>
            </select>
          </div>
        </div>

        <div class="field">
          <label for="ss-video-preset">Video workflow preset</label>
          <select id="ss-video-preset" v-model="videoPresetId" :disabled="presetsLoading">
            <option :value="null">None</option>
            <option v-for="p in ref2vPresets" :key="p.id" :value="p.id">
              {{ p.name }}
            </option>
          </select>
        </div>

        <p v-if="fieldsError" class="error">{{ fieldsError }}</p>

        <div class="section-actions">
          <button class="btn-primary" :disabled="saving" @click="saveFields">
            {{ saving ? 'Saving…' : 'Save' }}
          </button>
        </div>
      </section>

      <section class="section">
        <h4>Subjects</h4>

        <div v-for="s in store.tree?.subjects ?? []" :key="s.id" class="subject-row">
          <div class="subject-row-fields">
            <input
              type="text"
              class="subject-name"
              :value="s.name"
              placeholder="Name"
              @change="onSubjectName(s.id, $event)"
            />
            <input
              type="text"
              class="subject-desc"
              :value="s.description"
              placeholder="Description"
              @change="onSubjectDescription(s.id, $event)"
            />
            <input
              type="text"
              class="subject-lora"
              :value="s.lora_name ?? ''"
              placeholder="LoRA name"
              @change="onSubjectLoraName(s.id, $event)"
            />
            <input
              type="number"
              step="0.05"
              class="subject-strength"
              :value="s.lora_strength ?? ''"
              placeholder="Strength"
              @change="onSubjectLoraStrength(s.id, $event)"
            />
            <button
              type="button"
              class="remove-btn"
              title="Delete subject"
              @click="onDeleteSubject(s.id)"
            >
              &times;
            </button>
          </div>
          <div class="subject-row-fields">
            <input
              type="text"
              class="subject-voice"
              :value="s.voice ?? ''"
              placeholder="Voice (e.g. narrator, husky alto)"
              @change="onSubjectVoice(s.id, $event)"
            />
            <input
              type="text"
              class="subject-voice"
              :value="s.voice_ref_path ?? ''"
              placeholder="Voice ref (audio path)"
              @change="onSubjectVoiceRefPath(s.id, $event)"
            />
          </div>
          <div class="subject-ref-row">
            <img
              v-if="s.reference_path && refThumbFailed[s.id] !== s.reference_path"
              :src="thumbnailUrl(s.reference_path)"
              alt=""
              class="ref-thumb"
              @error="onRefThumbError(s.id, s.reference_path)"
            />
            <div v-else class="ref-thumb ref-thumb-empty" title="No reference image">
              <span>—</span>
            </div>
            <input
              type="text"
              class="subject-ref"
              :value="s.reference_path ?? ''"
              placeholder="Reference image path"
              @change="onSubjectReferencePath(s.id, $event)"
            />
            <button type="button" class="browse-btn" @click="picker = { id: s.id, slot: 1 }">
              Browse…
            </button>
            <button
              v-if="s.reference_path"
              type="button"
              class="remove-btn"
              title="Clear reference image"
              @click="onClearReference(s.id)"
            >
              &times;
            </button>
          </div>
          <div class="subject-ref-row">
            <img
              v-if="s.reference_path_2 && refThumbFailed2[s.id] !== s.reference_path_2"
              :src="thumbnailUrl(s.reference_path_2)"
              alt=""
              class="ref-thumb"
              @error="onRefThumbError(s.id, s.reference_path_2, 2)"
            />
            <div v-else class="ref-thumb ref-thumb-empty" title="No second reference image">
              <span>—</span>
            </div>
            <input
              type="text"
              class="subject-ref"
              :value="s.reference_path_2 ?? ''"
              placeholder="Second reference image path"
              @change="onSubjectReferencePath(s.id, $event, 2)"
            />
            <button type="button" class="browse-btn" @click="picker = { id: s.id, slot: 2 }">
              Browse…
            </button>
            <button
              v-if="s.reference_path_2"
              type="button"
              class="remove-btn"
              title="Clear second reference image"
              @click="onClearReference(s.id, 2)"
            >
              &times;
            </button>
          </div>

          <div class="describe-row">
            <button
              type="button"
              class="describe-btn"
              :disabled="(!s.reference_path && !s.reference_path_2) || describing[s.id]"
              @click="onDescribeSubject(s.id)"
            >
              {{ describing[s.id] ? 'Describing…' : 'Describe from refs' }}
            </button>
            <span class="hint">First call may take up to a minute while the model loads.</span>
          </div>
          <p v-if="describeErrors[s.id]" class="error inline">{{ describeErrors[s.id] }}</p>

          <div v-if="describeResult[s.id]" class="describe-card">
            <label>Description</label>
            <textarea readonly rows="3" :value="describeResult[s.id]!.description" />
            <template v-if="describeResult[s.id]!.voice">
              <label>Voice</label>
              <div class="describe-voice">{{ describeResult[s.id]!.voice }}</div>
            </template>
            <div class="describe-actions">
              <button type="button" class="btn-secondary" @click="onApplyDescribe(s.id)">
                Apply
              </button>
              <button type="button" class="btn-secondary" @click="onDismissDescribe(s.id)">
                Dismiss
              </button>
            </div>
          </div>

          <p v-if="subjectErrors[s.id]" class="error inline">{{ subjectErrors[s.id] }}</p>
        </div>

        <div v-if="(store.tree?.subjects.length ?? 0) === 0" class="empty-msg">
          No subjects yet.
        </div>

        <div class="add-subject-row">
          <input v-model="newSubjectName" type="text" placeholder="Name" />
          <input v-model="newSubjectDescription" type="text" placeholder="Description" />
          <button
            type="button"
            class="add-btn"
            :disabled="!newSubjectName.trim()"
            @click="onAddSubject"
          >
            Add subject
          </button>
        </div>
        <p v-if="addSubjectError" class="error inline">{{ addSubjectError }}</p>
      </section>

      <div class="dialog-actions">
        <button class="btn-secondary" @click="close">Close</button>
      </div>
    </div>

    <ReferenceImagePicker
      v-if="picker !== null"
      @select="onPickReference"
      @close="picker = null"
    />
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
  max-height: 88vh;
  overflow-y: auto;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
}

h3 {
  margin: 0 0 14px;
  font-size: 18px;
  color: var(--text-color);
}

.section {
  padding: 14px 0;
  border-top: 1px solid var(--surface-border);
}

.section:first-of-type {
  padding-top: 0;
  border-top: none;
}

h4 {
  margin: 0 0 12px;
  font-size: 14px;
  color: var(--text-color);
}

.field {
  margin-bottom: 12px;
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

label {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-color-secondary);
}

select,
input[type='number'],
input[type='text'],
textarea {
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

select:focus,
input:focus,
textarea:focus {
  outline: none;
  border-color: var(--primary-color);
}

textarea {
  resize: vertical;
}

.section-actions {
  display: flex;
  margin-top: 6px;
}

.subject-row {
  padding: 10px;
  border: 1px solid var(--surface-border);
  border-radius: 8px;
  margin-bottom: 10px;
}

.subject-row-fields {
  display: flex;
  gap: 8px;
  align-items: center;
}

.subject-name {
  flex: 1;
  min-width: 0;
}

.subject-desc {
  flex: 2;
  min-width: 0;
}

.subject-lora {
  flex: 1;
  min-width: 0;
}

.subject-strength {
  /* flex-basis, not width: the generic input[type='number'] { width: 100% }
     rule above outranks this class by specificity, and width:100% +
     flex-shrink:0 made this input swallow the whole row, collapsing the
     text inputs beside it to zero-width slivers. */
  flex: 0 0 80px;
}

.subject-voice {
  flex: 1;
  min-width: 0;
}

.subject-ref-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
}

.describe-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 8px;
}

.describe-btn {
  padding: 6px 12px;
  background: var(--surface-ground);
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  color: var(--text-color);
  font-size: 13px;
  cursor: pointer;
  flex-shrink: 0;
  white-space: nowrap;
}

.describe-btn:hover:not(:disabled) {
  background: var(--surface-hover);
}

.describe-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.hint {
  font-size: 11px;
  color: var(--text-color-secondary);
}

.describe-card {
  margin-top: 8px;
  padding: 10px;
  border: 1px solid var(--surface-border);
  border-radius: 8px;
  background: var(--surface-ground);
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.describe-card label {
  font-size: 11px;
}

.describe-card textarea {
  resize: vertical;
}

.describe-voice {
  padding: 6px 10px;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-card);
  color: var(--text-color);
  font-size: 13px;
}

.describe-actions {
  display: flex;
  gap: 8px;
}

.subject-ref {
  flex: 1;
  min-width: 0;
}

.ref-thumb {
  width: 44px;
  height: 44px;
  object-fit: cover;
  border-radius: 6px;
  border: 1px solid var(--surface-border);
  flex-shrink: 0;
  display: block;
}

.ref-thumb-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--surface-card);
  color: var(--text-color-secondary);
  font-size: 14px;
}

.browse-btn {
  padding: 6px 12px;
  background: var(--surface-ground);
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  color: var(--text-color);
  font-size: 13px;
  cursor: pointer;
  flex-shrink: 0;
  white-space: nowrap;
}

.browse-btn:hover {
  background: var(--surface-hover);
}

.remove-btn {
  background: none;
  border: none;
  color: var(--text-color-secondary);
  font-size: 18px;
  cursor: pointer;
  padding: 0 4px;
  line-height: 1;
  flex-shrink: 0;
}

.remove-btn:hover {
  color: var(--danger-color, #e53e3e);
}

.empty-msg {
  padding: 12px;
  text-align: center;
  color: var(--text-color-secondary);
  font-size: 13px;
}

.add-subject-row {
  display: flex;
  gap: 8px;
  margin-top: 4px;
}

.add-subject-row input {
  flex: 1;
}

.add-btn {
  padding: 6px 16px;
  background: var(--primary-color);
  border: none;
  border-radius: 6px;
  color: #fff;
  font-size: 13px;
  cursor: pointer;
  flex-shrink: 0;
}

.add-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.error {
  color: var(--danger-color, #e53e3e);
  font-size: 13px;
  margin: 4px 0 0;
}

.error.inline {
  margin-top: 6px;
}

.dialog-actions {
  display: flex;
  gap: 10px;
  margin-top: 16px;
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
</style>
