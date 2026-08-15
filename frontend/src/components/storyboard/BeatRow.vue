<script setup lang="ts">
import { ref, watch } from 'vue'
import { useStoryboardStore } from '../../stores/storyboard'
import type { Beat, DialogLine, Subject } from '../../types/storyboard'
import { CAMERA_AMPLITUDES, CAMERA_MOTIONS, CAMERA_SPEEDS } from '../../types/storyboard'

const props = defineProps<{ beat: Beat; subjects: Subject[] }>()
const store = useStoryboardStore()

// Local editable copies of the "commit on change" text fields, each paired
// with a "last synced from server" snapshot -- mirrors PanelDetail.vue's
// pattern. A field is only overwritten by `sync()` when local === snapshot
// (no pending edit for that field); commit() updates both together so the
// round trip isn't mistaken for a foreign change.
const actionVal = ref('')
const actionSnap = ref('')
const durationVal = ref('0')
const durationSnap = ref('0')
const soundVal = ref('')
const soundSnap = ref('')

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
}
// Keyed on id + updated_at (not just id) so a server-side rewrite of the
// currently-open beat -- e.g. a re-beat pass -- still reaches the field,
// not just a switch to a different beat.
watch(() => [props.beat.id, props.beat.updated_at], sync, { immediate: true })

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
  actionVal.value = (e.target as HTMLInputElement).value
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
  soundVal.value = (e.target as HTMLInputElement).value
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
  const raw = (e.target as HTMLInputElement).value
  void commitDialogLine(i, { text: raw })
}

async function remove(): Promise<void> {
  await store.removeBeat(props.beat.id)
}
</script>

<template>
  <div class="beat-row">
    <div class="beat-row-main">
      <div class="beat-field beat-field-action">
        <label class="beat-label">Action</label>
        <input
          type="text"
          :value="actionVal"
          @input="onActionInput"
          @change="onActionChange"
        />
      </div>
      <div class="beat-field beat-field-duration">
        <label class="beat-label">Dur (s)</label>
        <input
          type="number"
          step="0.5"
          min="0.5"
          :value="durationVal"
          @input="onDurationInput"
          @change="onDurationChange"
        />
      </div>
      <div class="beat-field">
        <label class="beat-label">Motion</label>
        <select :value="beat.camera_motion ?? ''" @change="onCameraChange('camera_motion', $event)">
          <option value="">—</option>
          <option v-for="m in CAMERA_MOTIONS" :key="m" :value="m">{{ m }}</option>
        </select>
      </div>
      <div class="beat-field">
        <label class="beat-label">Amplitude</label>
        <select
          :value="beat.camera_amplitude ?? ''"
          @change="onCameraChange('camera_amplitude', $event)"
        >
          <option value="">—</option>
          <option v-for="a in CAMERA_AMPLITUDES" :key="a" :value="a">{{ a }}</option>
        </select>
      </div>
      <div class="beat-field">
        <label class="beat-label">Speed</label>
        <select :value="beat.camera_speed ?? ''" @change="onCameraChange('camera_speed', $event)">
          <option value="">—</option>
          <option v-for="s in CAMERA_SPEEDS" :key="s" :value="s">{{ s }}</option>
        </select>
      </div>
      <button
        type="button"
        class="beat-cut-btn"
        :class="{ active: beat.is_cut }"
        title="Toggle hard cut before this beat"
        @click="toggleCut"
      >
        Cut
      </button>
      <div class="beat-field beat-field-sound">
        <label class="beat-label">Sound</label>
        <input type="text" :value="soundVal" @input="onSoundInput" @change="onSoundChange" />
      </div>
      <button type="button" class="beat-icon-btn" title="Delete beat" @click="remove">✕</button>
    </div>

    <div class="beat-dialog">
      <div v-for="(line, i) in beat.dialog" :key="i" class="dialog-line">
        <select
          class="dialog-speaker"
          :value="line.subject_id ?? ''"
          @change="onDialogSubjectChange(i, $event)"
        >
          <option value="">other voice</option>
          <option v-for="s in subjects" :key="s.id" :value="s.id">{{ s.name }}</option>
        </select>
        <input
          v-if="line.subject_id === null"
          type="text"
          class="dialog-voice"
          placeholder="voice"
          :value="line.voice ?? ''"
          @change="onDialogVoiceChange(i, $event)"
        />
        <input
          type="text"
          class="dialog-delivery"
          placeholder="delivery"
          :value="line.delivery ?? ''"
          @change="onDialogDeliveryChange(i, $event)"
        />
        <input
          type="text"
          class="dialog-language"
          placeholder="language"
          :value="line.language"
          @change="onDialogLanguageChange(i, $event)"
        />
        <input
          type="text"
          class="dialog-text"
          placeholder="line"
          :value="line.text"
          @change="onDialogTextChange(i, $event)"
        />
        <button
          type="button"
          class="beat-icon-btn"
          title="Remove line"
          @click="removeDialogLine(i)"
        >
          ✕
        </button>
      </div>
      <button type="button" class="dialog-add-btn" @click="addDialogLine">+ line</button>
    </div>
  </div>
</template>

<style scoped>
.beat-row {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-ground);
}

.beat-row-main {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  flex-wrap: wrap;
}

.beat-field {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 90px;
}

.beat-field-action {
  flex: 1;
  min-width: 140px;
}

.beat-field-duration {
  min-width: 64px;
}

.beat-field-sound {
  flex: 1;
  min-width: 120px;
}

.beat-label {
  font-size: 10px;
  font-weight: 600;
  color: var(--text-color-secondary);
  text-transform: uppercase;
  letter-spacing: 0.4px;
}

input[type='text'],
input[type='number'],
select {
  padding: 4px 6px;
  border: 1px solid var(--surface-border);
  border-radius: 5px;
  background: var(--surface-card);
  color: var(--text-color);
  font-size: 12px;
  font-family: inherit;
  width: 100%;
  box-sizing: border-box;
}

input:focus,
select:focus {
  outline: none;
  border-color: var(--primary-color);
}

.beat-cut-btn {
  padding: 5px 10px;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-card);
  color: var(--text-color-secondary);
  font-size: 11px;
  cursor: pointer;
  align-self: flex-end;
}

.beat-cut-btn.active {
  border-color: var(--primary-color);
  color: var(--primary-color);
  background: color-mix(in srgb, var(--primary-color) 12%, transparent);
}

.beat-icon-btn {
  border: 1px solid var(--surface-border);
  border-radius: 5px;
  background: var(--surface-card);
  color: var(--text-color-secondary);
  font-size: 11px;
  width: 24px;
  height: 24px;
  line-height: 1;
  cursor: pointer;
  align-self: flex-end;
  flex-shrink: 0;
}

.beat-icon-btn:hover {
  background: var(--surface-hover);
  color: var(--text-color);
}

.beat-dialog {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding-left: 4px;
  border-left: 2px solid var(--surface-border);
}

.dialog-line {
  display: flex;
  align-items: center;
  gap: 6px;
}

.dialog-speaker {
  flex: 0 0 110px;
}

.dialog-voice {
  flex: 0 0 90px;
}

.dialog-delivery {
  flex: 0 0 90px;
}

.dialog-language {
  flex: 0 0 80px;
}

.dialog-text {
  flex: 1;
  min-width: 100px;
}

.dialog-add-btn {
  align-self: flex-start;
  padding: 3px 10px;
  border: 1px dashed var(--surface-border);
  border-radius: 5px;
  background: none;
  color: var(--text-color-secondary);
  font-size: 11px;
  cursor: pointer;
}

.dialog-add-btn:hover {
  background: var(--surface-hover);
  color: var(--text-color);
}
</style>
