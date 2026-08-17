<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useStoryboardStore } from '../../stores/storyboard'
import type { Beat, DialogLine, Subject } from '../../types/storyboard'
import {
  ANGLES,
  CAMERA_AMPLITUDES,
  CAMERA_MOTIONS,
  CAMERA_SPEEDS,
  LENSES,
  SHOT_SIZES,
} from '../../types/storyboard'
import BeatImages from './BeatImages.vue'
import DeleteImagesDialog from './DeleteImagesDialog.vue'

const props = defineProps<{
  beat: Beat
  subjects: Subject[]
}>()
const store = useStoryboardStore()

// Local editable copies of the "commit on change" text fields, each paired
// with a "last synced from server" snapshot -- mirrors PanelDetail.vue's /
// the former BeatRow.vue's pattern. A field is only overwritten by `sync()`
// when local === snapshot (no pending edit for that field); commit() updates
// both together so the round trip isn't mistaken for a foreign change.
const actionVal = ref('')
const actionSnap = ref('')
const durationVal = ref('0')
const durationSnap = ref('0')
const soundVal = ref('')
const soundSnap = ref('')
// Framing + prompt fields moved down from PanelDetail.vue -- these are now
// beat-scoped (each beat frames and prompts its own image), not panel-scoped.
const shotSizeVal = ref('')
const shotSizeSnap = ref('')
const angleVal = ref('')
const angleSnap = ref('')
const lensVal = ref('')
const lensSnap = ref('')
const promptVal = ref('')
const promptSnap = ref('')

function sync(): void {
  if (actionVal.value === actionSnap.value) {
    actionVal.value = props.beat.action
    actionSnap.value = props.beat.action
  }
  const d = String(props.beat.duration_s)
  if (durationVal.value === durationSnap.value) {
    durationVal.value = d
    durationSnap.value = d
  }
  const s = props.beat.sound ?? ''
  if (soundVal.value === soundSnap.value) {
    soundVal.value = s
    soundSnap.value = s
  }
  const shotSize = props.beat.shot_size ?? ''
  if (shotSizeVal.value === shotSizeSnap.value) {
    shotSizeVal.value = shotSize
    shotSizeSnap.value = shotSize
  }
  const angle = props.beat.angle ?? ''
  if (angleVal.value === angleSnap.value) {
    angleVal.value = angle
    angleSnap.value = angle
  }
  const lens = props.beat.lens ?? ''
  if (lensVal.value === lensSnap.value) {
    lensVal.value = lens
    lensSnap.value = lens
  }
  const prompt = props.beat.prompt ?? ''
  if (promptVal.value === promptSnap.value) {
    promptVal.value = prompt
    promptSnap.value = prompt
  }
}
// Keyed on id + updated_at (not just id) so a server-side rewrite of the
// currently-open beat -- e.g. a re-beat pass, or a synthesis run rewriting
// `prompt` -- still reaches the fields, not just a switch to a different
// beat.
watch(() => [props.beat.id, props.beat.updated_at], sync, { immediate: true })

function subjectName(id: number): string {
  return props.subjects.find((s) => s.id === id)?.name ?? `#${id}`
}

function toggleSubject(id: number): void {
  const current = [...props.beat.subject_ids]
  const idx = current.indexOf(id)
  if (idx >= 0) current.splice(idx, 1)
  else current.push(id)
  void store.patchBeatFields(props.beat.id, { subject_ids: current })
}

function promoteSubject(id: number): void {
  const current = props.beat.subject_ids.filter((x) => x !== id)
  current.unshift(id)
  void store.patchBeatFields(props.beat.id, { subject_ids: current })
}

function commitShotSize(e: Event): void {
  const val = (e.target as HTMLSelectElement).value
  shotSizeVal.value = val
  shotSizeSnap.value = val
  const next = val === '' ? null : val
  if (next === props.beat.shot_size) return
  void store.patchBeatFields(props.beat.id, { shot_size: next })
}

function commitAngle(e: Event): void {
  const val = (e.target as HTMLSelectElement).value
  angleVal.value = val
  angleSnap.value = val
  const next = val === '' ? null : val
  if (next === props.beat.angle) return
  void store.patchBeatFields(props.beat.id, { angle: next })
}

function commitLens(e: Event): void {
  const val = (e.target as HTMLSelectElement).value
  lensVal.value = val
  lensSnap.value = val
  const next = val === '' ? null : val
  if (next === props.beat.lens) return
  void store.patchBeatFields(props.beat.id, { lens: next })
}

function commitPrompt(e: Event): void {
  const val = (e.target as HTMLTextAreaElement).value
  promptVal.value = val
  promptSnap.value = val
  if (val === (props.beat.prompt ?? '')) return
  void store.patchBeatFields(props.beat.id, { prompt: val })
}

function unlockPrompt(): void {
  void store.patchBeatFields(props.beat.id, { prompt_locked: 0 })
}

const promptStatusLabel = computed(() => {
  if (props.beat.prompt_locked === 1) return '🔒 edited'
  if (props.beat.prompt_source === 'brief') return 'brief fallback'
  if (props.beat.prompt_source === 'llm') return 'synthesized'
  return '—'
})

const hasActiveJob = computed(() => store.beatJobState.has(props.beat.id))

function resynth(): void {
  void store.synthesize([props.beat.id], true)
}

async function commit(field: 'action' | 'duration_s' | 'sound', raw: string): Promise<void> {
  if (field === 'action') {
    actionSnap.value = raw
  }
  if (field === 'duration_s') {
    durationSnap.value = raw
  }
  if (field === 'sound') {
    soundSnap.value = raw
  }
  const body =
    field === 'duration_s'
      ? { duration_s: Math.max(0.5, Number(raw) || props.beat.duration_s) }
      : field === 'sound'
        ? { sound: raw.trim() || null }
        : { action: raw }
  await store.patchBeatFields(props.beat.id, body)
}

function onActionInput(e: Event): void {
  actionVal.value = (e.target as HTMLTextAreaElement).value
}
function onActionChange(): void {
  void commit('action', actionVal.value)
}

function onDurationInput(e: Event): void {
  durationVal.value = (e.target as HTMLInputElement).value
}
function onDurationChange(): void {
  void commit('duration_s', durationVal.value)
}

function onSoundInput(e: Event): void {
  soundVal.value = (e.target as HTMLTextAreaElement).value
}
function onSoundChange(): void {
  void commit('sound', soundVal.value)
}

async function commitSelect(
  field: 'camera_motion' | 'camera_amplitude' | 'camera_speed',
  value: string,
): Promise<void> {
  await store.patchBeatFields(props.beat.id, { [field]: value || null })
}

function onCameraChange(
  field: 'camera_motion' | 'camera_amplitude' | 'camera_speed',
  e: Event,
): void {
  void commitSelect(field, (e.target as HTMLSelectElement).value)
}

async function toggleCut(): Promise<void> {
  await store.patchBeatFields(props.beat.id, { is_cut: props.beat.is_cut ? 0 : 1 })
}

async function addDialogLine(): Promise<void> {
  const lines: DialogLine[] = [
    ...props.beat.dialog,
    { subject_id: null, voice: null, delivery: null, language: 'English', text: '' },
  ]
  await store.patchBeatFields(props.beat.id, { dialog: lines })
}

async function commitDialogLine(i: number, patchLine: Partial<DialogLine>): Promise<void> {
  const lines = props.beat.dialog.map((l, j) => (j === i ? { ...l, ...patchLine } : l))
  await store.patchBeatFields(props.beat.id, { dialog: lines })
}

async function removeDialogLine(i: number): Promise<void> {
  await store.patchBeatFields(props.beat.id, {
    dialog: props.beat.dialog.filter((_, j) => j !== i),
  })
}

function onDialogSubjectChange(i: number, e: Event): void {
  const raw = (e.target as HTMLSelectElement).value
  void commitDialogLine(i, { subject_id: raw === '' ? null : Number(raw) })
}
function onDialogVoiceChange(i: number, e: Event): void {
  const raw = (e.target as HTMLInputElement).value
  void commitDialogLine(i, { voice: raw.trim() || null })
}
function onDialogDeliveryChange(i: number, e: Event): void {
  const raw = (e.target as HTMLInputElement).value
  void commitDialogLine(i, { delivery: raw.trim() || null })
}
function onDialogLanguageChange(i: number, e: Event): void {
  const raw = (e.target as HTMLInputElement).value
  void commitDialogLine(i, { language: raw })
}
function onDialogTextChange(i: number, e: Event): void {
  const raw = (e.target as HTMLTextAreaElement).value
  void commitDialogLine(i, { text: raw })
}

// Deleting a beat with generated images asks what happens to them (purge /
// keep in library / cancel) via DeleteImagesDialog, mirroring PanelGrid's /
// SceneStrip's panel/scene delete flow; a beat with no images gets a plain
// confirm.
const pendingDelete = ref(false)

function remove(): void {
  if (props.beat.images.length === 0) {
    if (!confirm('Delete this beat?')) return
    void store.removeBeat(props.beat.id)
    return
  }
  pendingDelete.value = true
}

async function confirmDelete(purgeImages: boolean): Promise<void> {
  pendingDelete.value = false
  await store.removeBeat(props.beat.id, purgeImages)
}
</script>

<template>
  <div class="bf-root">
    <div class="bf-field">
      <label class="bf-label">Action</label>
      <textarea rows="3" :value="actionVal" @input="onActionInput" @change="onActionChange" />
    </div>

    <div class="bf-field-row">
      <div class="bf-field">
        <label class="bf-label">Shot size</label>
        <select :value="shotSizeVal" @change="commitShotSize">
          <option value="">—</option>
          <option v-for="s in SHOT_SIZES" :key="s" :value="s">{{ s }}</option>
        </select>
      </div>
      <div class="bf-field">
        <label class="bf-label">Angle</label>
        <select :value="angleVal" @change="commitAngle">
          <option value="">—</option>
          <option v-for="a in ANGLES" :key="a" :value="a">{{ a }}</option>
        </select>
      </div>
      <div class="bf-field">
        <label class="bf-label">Lens</label>
        <select :value="lensVal" @change="commitLens">
          <option value="">—</option>
          <option v-for="l in LENSES" :key="l" :value="l">{{ l }}</option>
        </select>
      </div>
    </div>

    <div class="bf-field">
      <label class="bf-label">Subjects</label>
      <div v-if="beat.subject_ids.length" class="subject-chips">
        <button
          v-for="(sid, i) in beat.subject_ids"
          :key="sid"
          type="button"
          class="subject-chip"
          :class="{ primary: i === 0 }"
          :title="i === 0 ? 'Primary subject' : 'Click to make primary'"
          @click="promoteSubject(sid)"
        >
          <span v-if="i === 0" class="subject-chip-star">★</span>
          {{ subjectName(sid) }}
        </button>
      </div>
      <div class="subject-checklist">
        <label v-for="s in subjects" :key="s.id" class="subject-check">
          <input
            type="checkbox"
            :checked="beat.subject_ids.includes(s.id)"
            @change="toggleSubject(s.id)"
          />
          {{ s.name }}
        </label>
        <span v-if="subjects.length === 0" class="bf-hint">No subjects defined yet.</span>
      </div>
    </div>

    <div class="bf-field">
      <div class="bf-prompt-header">
        <label class="bf-label">Prompt</label>
        <span class="bf-prompt-status">{{ promptStatusLabel }}</span>
        <button
          v-if="beat.prompt_locked === 1"
          type="button"
          class="bf-link-btn"
          @click="unlockPrompt"
        >
          Unlock
        </button>
      </div>
      <textarea :value="promptVal" rows="4" @change="commitPrompt" />
      <button
        type="button"
        class="bf-reroll-btn bf-resynth-btn"
        :disabled="hasActiveJob || store.synthesis.running"
        @click="resynth"
      >
        Re-synth prompt
      </button>
    </div>

    <BeatImages :beat="beat" />

    <div class="bf-field-row">
      <div class="bf-field bf-field-duration">
        <label class="bf-label">Duration (s)</label>
        <input
          type="number"
          step="0.5"
          min="0.5"
          :value="durationVal"
          @input="onDurationInput"
          @change="onDurationChange"
        />
      </div>
      <button
        type="button"
        class="bf-cut-btn"
        :class="{ active: beat.is_cut }"
        title="Toggle hard cut before this beat"
        @click="toggleCut"
      >
        Cut
      </button>
    </div>

    <div class="bf-field-row">
      <div class="bf-field">
        <label class="bf-label">Motion</label>
        <select :value="beat.camera_motion ?? ''" @change="onCameraChange('camera_motion', $event)">
          <option value="">—</option>
          <option v-for="m in CAMERA_MOTIONS" :key="m" :value="m">{{ m }}</option>
        </select>
      </div>
      <div class="bf-field">
        <label class="bf-label">Amplitude</label>
        <select
          :value="beat.camera_amplitude ?? ''"
          @change="onCameraChange('camera_amplitude', $event)"
        >
          <option value="">—</option>
          <option v-for="a in CAMERA_AMPLITUDES" :key="a" :value="a">{{ a }}</option>
        </select>
      </div>
      <div class="bf-field">
        <label class="bf-label">Speed</label>
        <select :value="beat.camera_speed ?? ''" @change="onCameraChange('camera_speed', $event)">
          <option value="">—</option>
          <option v-for="s in CAMERA_SPEEDS" :key="s" :value="s">{{ s }}</option>
        </select>
      </div>
    </div>

    <div class="bf-field">
      <label class="bf-label">Sound</label>
      <textarea rows="2" :value="soundVal" @input="onSoundInput" @change="onSoundChange" />
    </div>

    <div class="bf-field">
      <label class="bf-label">Dialog</label>
      <div class="bf-dialog">
        <div v-for="(line, i) in beat.dialog" :key="i" class="bf-dialog-line">
          <div class="bf-dialog-line-row">
            <select
              class="bf-dialog-speaker"
              :value="line.subject_id ?? ''"
              @change="onDialogSubjectChange(i, $event)"
            >
              <option value="">other voice</option>
              <option v-for="s in subjects" :key="s.id" :value="s.id">{{ s.name }}</option>
            </select>
            <input
              v-if="line.subject_id === null"
              type="text"
              class="bf-dialog-voice"
              placeholder="voice"
              :value="line.voice ?? ''"
              @change="onDialogVoiceChange(i, $event)"
            />
            <input
              type="text"
              class="bf-dialog-delivery"
              placeholder="delivery"
              :value="line.delivery ?? ''"
              @change="onDialogDeliveryChange(i, $event)"
            />
            <input
              type="text"
              class="bf-dialog-language"
              placeholder="language"
              :value="line.language"
              @change="onDialogLanguageChange(i, $event)"
            />
            <button
              type="button"
              class="bf-icon-btn"
              title="Remove line"
              @click="removeDialogLine(i)"
            >
              ✕
            </button>
          </div>
          <textarea
            class="bf-dialog-text"
            rows="2"
            placeholder="spoken line"
            :value="line.text"
            @change="onDialogTextChange(i, $event)"
          />
        </div>
        <button type="button" class="bf-dialog-add-btn" @click="addDialogLine">+ line</button>
      </div>
    </div>

    <button type="button" class="bf-delete-btn" @click="remove">Delete beat</button>
  </div>

  <DeleteImagesDialog
    v-if="pendingDelete"
    title="Delete beat?"
    message="This beat has generated images. Delete them permanently, or keep them visible in the media library?"
    :image-count="beat.images.length"
    @purge="confirmDelete(true)"
    @keep="confirmDelete(false)"
    @cancel="pendingDelete = false"
  />
</template>

<style scoped>
.bf-root {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.bf-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.bf-field-row {
  display: flex;
  align-items: flex-end;
  gap: 10px;
}

.bf-field-row .bf-field {
  flex: 1;
  min-width: 0;
}

.bf-field-duration {
  flex: 0 0 120px;
}

.bf-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-color-secondary);
  text-transform: uppercase;
  letter-spacing: 0.4px;
}

.bf-hint {
  font-size: 11px;
  color: var(--text-color-secondary);
}

.subject-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 4px;
}

.subject-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 10px;
  border-radius: 999px;
  border: 1px solid var(--surface-border);
  background: var(--surface-ground);
  color: var(--text-color);
  font-size: 12px;
  cursor: pointer;
}

.subject-chip:hover {
  background: var(--surface-hover);
}

.subject-chip.primary {
  border-color: var(--primary-color);
  color: var(--primary-color);
}

.subject-chip-star {
  font-size: 10px;
}

.subject-checklist {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
  padding: 4px 0;
}

.subject-check {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  color: var(--text-color);
  cursor: pointer;
}

.bf-prompt-header {
  display: flex;
  align-items: center;
  gap: 10px;
}

.bf-prompt-status {
  font-size: 11px;
  color: var(--text-color-secondary);
  flex: 1;
}

.bf-link-btn {
  background: none;
  border: none;
  padding: 0;
  color: var(--primary-color);
  cursor: pointer;
  font-size: 11px;
  text-decoration: underline;
}

.bf-reroll-btn {
  padding: 3px 10px;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-ground);
  color: var(--text-color);
  font-size: 11px;
  cursor: pointer;
  flex-shrink: 0;
}

.bf-reroll-btn:hover:not(:disabled) {
  background: var(--surface-hover);
}

.bf-reroll-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.bf-resynth-btn {
  align-self: flex-start;
  margin-top: 6px;
}

input[type='text'],
input[type='number'],
select,
textarea {
  padding: 5px 8px;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-ground);
  color: var(--text-color);
  font-size: 13px;
  font-family: inherit;
  width: 100%;
  box-sizing: border-box;
}

input:focus,
select:focus,
textarea:focus {
  outline: none;
  border-color: var(--primary-color);
}

textarea {
  resize: vertical;
}

.bf-cut-btn {
  padding: 6px 14px;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-ground);
  color: var(--text-color-secondary);
  font-size: 12px;
  cursor: pointer;
  flex-shrink: 0;
}

.bf-cut-btn.active {
  border-color: var(--primary-color);
  color: var(--primary-color);
  background: color-mix(in srgb, var(--primary-color) 12%, transparent);
}

.bf-dialog {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-left: 8px;
  border-left: 2px solid var(--surface-border);
}

.bf-dialog-line {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.bf-dialog-line-row {
  display: flex;
  align-items: center;
  gap: 6px;
}

.bf-dialog-speaker {
  flex: 0 0 100px;
}

.bf-dialog-voice {
  flex: 0 0 80px;
}

.bf-dialog-delivery {
  flex: 0 0 80px;
}

.bf-dialog-language {
  flex: 0 0 72px;
}

.bf-dialog-text {
  width: 100%;
}

.bf-icon-btn {
  border: 1px solid var(--surface-border);
  border-radius: 5px;
  background: var(--surface-card);
  color: var(--text-color-secondary);
  font-size: 11px;
  width: 24px;
  height: 24px;
  line-height: 1;
  cursor: pointer;
  flex-shrink: 0;
}

.bf-icon-btn:hover {
  background: var(--surface-hover);
  color: var(--text-color);
}

.bf-dialog-add-btn {
  align-self: flex-start;
  padding: 3px 10px;
  border: 1px dashed var(--surface-border);
  border-radius: 5px;
  background: none;
  color: var(--text-color-secondary);
  font-size: 11px;
  cursor: pointer;
}

.bf-dialog-add-btn:hover {
  background: var(--surface-hover);
  color: var(--text-color);
}

.bf-delete-btn {
  align-self: flex-start;
  padding: 6px 14px;
  border: 1px solid var(--danger-color, #e53e3e);
  border-radius: 6px;
  background: none;
  color: var(--danger-color, #e53e3e);
  font-size: 12px;
  cursor: pointer;
}

.bf-delete-btn:hover {
  background: color-mix(in srgb, var(--danger-color, #e53e3e) 12%, transparent);
}
</style>
