<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { thumbnailUrl } from '../../api/client'
import { useStoryboardStore } from '../../stores/storyboard'
import type { Beat, BeatImage, DialogLine, Subject } from '../../types/storyboard'
import {
  ANGLES,
  CAMERA_AMPLITUDES,
  CAMERA_MOTIONS,
  CAMERA_SPEEDS,
  LENSES,
  SHOT_SIZES,
} from '../../types/storyboard'
import type { Media } from '../../types/media'
import { isVideoPath } from '../../utils/path'
import BeatPromptDialog from './BeatPromptDialog.vue'
import DeleteImagesDialog from './DeleteImagesDialog.vue'
import MediaViewer from '../viewer/MediaViewer.vue'

const props = defineProps<{
  beat: Beat
  index: number
  subjects: Subject[]
  selected: boolean
  canUp: boolean
  canDown: boolean
}>()
const emit = defineEmits<{ (e: 'select'): void; (e: 'move', dir: -1 | 1): void }>()
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

// ---- ported from BeatImages.vue -----------------------------------------

function candidateTooltip(img: BeatImage): string {
  return `seed ${img.seed ?? '—'} · variant ${img.variant_index}`
}

// Keeper click toggles selection: clicking the current keeper again clears
// it (selected_image_id -> null), matching the pre-reorg PanelDetail
// behavior this was lifted from. The toggle is deferred briefly so a
// double-click (open in viewer -- the only way to *play* a video candidate)
// doesn't also fire the toggle twice.
let clickTimer: number | null = null

function onCandidateClick(img: BeatImage): void {
  if (clickTimer !== null) clearTimeout(clickTimer)
  clickTimer = window.setTimeout(() => {
    clickTimer = null
    const next = props.beat.selected_image_id === img.id ? null : img.id
    void store.selectImage(props.beat.id, next)
  }, 250)
}

function onCandidateDblClick(idx: number): void {
  if (clickTimer !== null) {
    clearTimeout(clickTimer)
    clickTimer = null
  }
  viewerIndex.value = idx
}

const viewerIndex = ref<number | null>(null)
const viewerMedia = computed<Media[]>(() =>
  props.beat.images.map(
    (img) =>
      ({
        file_path: img.file_path,
        is_favorite: false,
        is_video: isVideoPath(img.file_path),
        playback_speed: null,
        width: 0,
        height: 0,
        file_size: 0,
        frame_rate: null,
        duration: null,
      }) as Media,
  ),
)

// ---- new to the card ------------------------------------------------------

const promptOpen = ref(false)

const jobChip = computed(() => store.beatJobState.get(props.beat.id)?.state ?? null)
</script>

<template>
  <article class="bc" :class="{ selected }" @click="emit('select')">
    <div class="bc-head">
      <span class="bc-label" :class="{ selected }">BEAT {{ index + 1 }}</span>
      <span v-if="beat.is_cut === 1" class="bc-cut-flag">hard cut</span>
      <span class="bc-duration">{{ beat.duration_s.toFixed(1) }}s</span>
      <span v-if="jobChip" class="bc-chip">{{ jobChip }}</span>
      <div class="bc-head-actions">
        <button
          type="button"
          class="bc-icon-btn"
          title="Move up"
          :disabled="!canUp"
          @click.stop="emit('move', -1)"
        >
          ↑
        </button>
        <button
          type="button"
          class="bc-icon-btn"
          title="Move down"
          :disabled="!canDown"
          @click.stop="emit('move', 1)"
        >
          ↓
        </button>
        <button
          type="button"
          class="bc-icon-btn bc-icon-btn--danger"
          title="Delete beat"
          @click.stop="remove()"
        >
          ✕
        </button>
      </div>
    </div>

    <div class="bc-body">
      <div class="bc-col-left">
        <div class="bc-field">
          <label class="bc-label-eyebrow">Action</label>
          <textarea rows="2" :value="actionVal" @input="onActionInput" @change="onActionChange" />
        </div>

        <div class="bc-field-row">
          <div class="bc-field bc-field-flex1">
            <label class="bc-label-eyebrow">Shot size</label>
            <select :value="shotSizeVal" @change="commitShotSize">
              <option value="">—</option>
              <option v-for="s in SHOT_SIZES" :key="s" :value="s">{{ s }}</option>
            </select>
          </div>
          <div class="bc-field bc-field-flex1">
            <label class="bc-label-eyebrow">Angle</label>
            <select :value="angleVal" @change="commitAngle">
              <option value="">—</option>
              <option v-for="a in ANGLES" :key="a" :value="a">{{ a }}</option>
            </select>
          </div>
          <div class="bc-field bc-field-flex1">
            <label class="bc-label-eyebrow">Lens</label>
            <select :value="lensVal" @change="commitLens">
              <option value="">—</option>
              <option v-for="l in LENSES" :key="l" :value="l">{{ l }}</option>
            </select>
          </div>
          <div class="bc-field bc-field-duration">
            <label class="bc-label-eyebrow">Dur (s)</label>
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
            class="bc-cut-btn"
            :class="{ active: beat.is_cut }"
            title="Toggle hard cut before this beat"
            @click="toggleCut"
          >
            Cut
          </button>
        </div>

        <div class="bc-field">
          <label class="bc-label-eyebrow">Camera move</label>
          <div class="bc-camera-row">
            <select
              class="bc-camera-motion"
              :value="beat.camera_motion ?? ''"
              @change="onCameraChange('camera_motion', $event)"
            >
              <option value="">—</option>
              <option v-for="m in CAMERA_MOTIONS" :key="m" :value="m">{{ m }}</option>
            </select>
            <select
              class="bc-camera-amp"
              :value="beat.camera_amplitude ?? ''"
              @change="onCameraChange('camera_amplitude', $event)"
            >
              <option value="">—</option>
              <option v-for="a in CAMERA_AMPLITUDES" :key="a" :value="a">{{ a }}</option>
            </select>
            <select
              class="bc-camera-speed"
              :value="beat.camera_speed ?? ''"
              @change="onCameraChange('camera_speed', $event)"
            >
              <option value="">—</option>
              <option v-for="s in CAMERA_SPEEDS" :key="s" :value="s">{{ s }}</option>
            </select>
          </div>
        </div>

        <div class="bc-field">
          <label class="bc-label-eyebrow">Cast</label>
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
            <span v-if="subjects.length === 0" class="bc-hint">No subjects defined yet.</span>
          </div>
        </div>
      </div>

      <div class="bc-col-right">
        <div class="bc-field">
          <div class="bc-prompt-header">
            <label class="bc-label-eyebrow">Prompt</label>
            <span class="bc-prompt-status">{{ promptStatusLabel }}</span>
            <button
              v-if="beat.prompt_locked === 1"
              type="button"
              class="bc-link-btn"
              @click="unlockPrompt"
            >
              Unlock
            </button>
            <button
              type="button"
              class="bc-icon-btn"
              title="Open prompt in a larger editor"
              @click.stop="promptOpen = true"
            >
              <span class="pi pi-window-maximize" />
            </button>
          </div>
          <textarea :value="promptVal" rows="6" @change="commitPrompt" />
        </div>

        <div class="bc-field">
          <div class="bc-candidates-header">
            <label class="bc-label-eyebrow bc-flex1">Candidates</label>
            <button
              type="button"
              class="bc-xs-btn"
              :disabled="hasActiveJob || store.synthesis.running"
              @click="resynth"
            >
              Re-synth
            </button>
            <button
              type="button"
              class="bc-xs-btn"
              :disabled="hasActiveJob"
              @click="store.generate([beat.id])"
            >
              {{ hasActiveJob ? 'Generating…' : 'Reroll' }}
            </button>
          </div>
          <div class="candidates-row">
            <div
              v-for="(img, idx) in beat.images"
              :key="img.id"
              class="candidate-tile"
              :class="{ selected: img.id === beat.selected_image_id }"
              :title="candidateTooltip(img)"
              @click="onCandidateClick(img)"
              @dblclick="onCandidateDblClick(idx)"
            >
              <img :src="thumbnailUrl(img.file_path)" alt="" class="candidate-img" />
              <span v-if="img.id === beat.selected_image_id" class="candidate-check">✓</span>
              <button
                type="button"
                class="candidate-expand"
                title="View full size"
                @click.stop="viewerIndex = idx"
              >
                <span class="pi pi-search-plus" />
              </button>
            </div>
            <div v-if="beat.images.length === 0" class="candidates-empty">No candidates yet.</div>
          </div>
        </div>

        <div class="bc-field">
          <label class="bc-label-eyebrow">Sound</label>
          <textarea
            rows="2"
            placeholder="ambient, effects, music"
            :value="soundVal"
            @input="onSoundInput"
            @change="onSoundChange"
          />
        </div>

        <div class="bc-field">
          <label class="bc-label-eyebrow">Dialog</label>
          <div class="bc-dialog">
            <div v-for="(line, i) in beat.dialog" :key="i" class="bc-dialog-line">
              <div class="bc-dialog-line-row">
                <select
                  class="bc-dialog-speaker"
                  :value="line.subject_id ?? ''"
                  @change="onDialogSubjectChange(i, $event)"
                >
                  <option value="">other voice</option>
                  <option v-for="s in subjects" :key="s.id" :value="s.id">{{ s.name }}</option>
                </select>
                <input
                  v-if="line.subject_id === null"
                  type="text"
                  class="bc-dialog-voice"
                  placeholder="voice"
                  :value="line.voice ?? ''"
                  @change="onDialogVoiceChange(i, $event)"
                />
                <input
                  type="text"
                  class="bc-dialog-delivery"
                  placeholder="delivery"
                  :value="line.delivery ?? ''"
                  @change="onDialogDeliveryChange(i, $event)"
                />
                <input
                  type="text"
                  class="bc-dialog-language"
                  placeholder="language"
                  :value="line.language"
                  @change="onDialogLanguageChange(i, $event)"
                />
                <button
                  type="button"
                  class="bc-icon-btn"
                  title="Remove line"
                  @click="removeDialogLine(i)"
                >
                  ✕
                </button>
              </div>
              <textarea
                class="bc-dialog-text"
                rows="2"
                placeholder="spoken line"
                :value="line.text"
                @change="onDialogTextChange(i, $event)"
              />
            </div>
            <button type="button" class="bc-dialog-add-btn" @click="addDialogLine">
              + line
            </button>
          </div>
        </div>
      </div>
    </div>
  </article>

  <BeatPromptDialog v-if="promptOpen" :beat="beat" :index="index" @close="promptOpen = false" />

  <DeleteImagesDialog
    v-if="pendingDelete"
    title="Delete beat?"
    message="This beat has generated images. Delete them permanently, or keep them visible in the media library?"
    :image-count="beat.images.length"
    @purge="confirmDelete(true)"
    @keep="confirmDelete(false)"
    @cancel="pendingDelete = false"
  />

  <MediaViewer
    v-if="viewerIndex !== null"
    :media-list="viewerMedia"
    :initial-index="viewerIndex"
    :allow-destructive="false"
    @close="viewerIndex = null"
  />
</template>

<style scoped>
.bc {
  border: 1px solid var(--surface-border);
  border-radius: 8px;
  background: var(--surface-card);
  padding: 12px 14px 14px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  cursor: pointer;
}

.bc.selected {
  border-color: var(--primary-color);
  box-shadow: 0 0 0 1px var(--primary-color);
}

.bc-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.bc-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-color-secondary);
}

.bc-label.selected {
  color: var(--primary-color);
}

.bc-cut-flag {
  font-size: 11px;
  font-weight: 600;
  color: var(--warn);
}

.bc-duration {
  font-size: 11px;
  color: var(--text-color-secondary);
  font-variant-numeric: tabular-nums;
}

.bc-chip {
  font-size: 12px;
  color: var(--text-color-secondary);
  background: var(--surface-hover);
  padding: 4px 10px;
  border-radius: 999px;
}

.bc-head-actions {
  margin-left: auto;
  display: flex;
  gap: 4px;
}

.bc-icon-btn {
  width: 22px;
  height: 22px;
  border: 1px solid var(--surface-border);
  border-radius: 5px;
  background: var(--surface-card);
  color: var(--text-color-secondary);
  font-size: 11px;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  transition:
    background 0.15s,
    color 0.15s;
}

.bc-icon-btn:hover {
  background: var(--surface-hover);
}

.bc-icon-btn--danger:hover {
  color: var(--danger-color);
}

.bc-icon-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.bc-body {
  display: flex;
  gap: 14px;
  align-items: flex-start;
}

.bc-col-left {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.bc-col-right {
  flex: 0 0 340px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.bc-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.bc-field-row {
  display: flex;
  align-items: flex-end;
  gap: 10px;
}

.bc-field-flex1 {
  flex: 1;
  min-width: 0;
}

.bc-field-duration {
  flex: 0 0 84px;
}

.bc-label-eyebrow {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-color-secondary);
  text-transform: uppercase;
  letter-spacing: 0.4px;
}

.bc-hint {
  font-size: 11px;
  color: var(--text-color-secondary);
}

.bc-camera-row {
  display: flex;
  gap: 6px;
}

.bc-camera-motion {
  flex: 2;
}

.bc-camera-amp,
.bc-camera-speed {
  flex: 1;
}

.subject-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 2px;
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
  transition: background 0.15s;
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

.bc-prompt-header {
  display: flex;
  align-items: center;
  gap: 10px;
}

.bc-prompt-status {
  font-size: 11px;
  color: var(--text-color-secondary);
  flex: 1;
}

.bc-link-btn {
  background: none;
  border: none;
  padding: 0;
  color: var(--primary-color);
  cursor: pointer;
  font-size: 11px;
  text-decoration: underline;
}

.bc-candidates-header {
  display: flex;
  align-items: center;
  gap: 10px;
}

.bc-flex1 {
  flex: 1;
}

.bc-xs-btn {
  padding: 3px 10px;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-ground);
  color: var(--text-color);
  font-size: 11px;
  cursor: pointer;
  flex-shrink: 0;
  transition: background 0.15s;
}

.bc-xs-btn:hover:not(:disabled) {
  background: var(--surface-hover);
}

.bc-xs-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
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

.bc-cut-btn {
  padding: 6px 14px;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-ground);
  color: var(--text-color-secondary);
  font-size: 12px;
  cursor: pointer;
  flex-shrink: 0;
}

.bc-cut-btn.active {
  border-color: var(--primary-color);
  color: var(--primary-color);
  background: color-mix(in srgb, var(--primary-color) 12%, transparent);
}

.candidates-row {
  display: flex;
  gap: 10px;
  overflow-x: auto;
  padding-bottom: 4px;
}

.candidate-tile {
  position: relative;
  flex: 0 0 auto;
  width: 96px;
  height: 96px;
  border-radius: 6px;
  overflow: hidden;
  border: 2px solid transparent;
  background: var(--surface-ground);
  cursor: pointer;
}

.candidate-tile.selected {
  border-color: var(--primary-color);
}

.candidate-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.candidate-check {
  position: absolute;
  top: 4px;
  left: 4px;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: var(--primary-color);
  color: #fff;
  font-size: 11px;
  line-height: 18px;
  text-align: center;
}

.candidate-expand {
  position: absolute;
  top: 4px;
  right: 4px;
  width: 22px;
  height: 22px;
  border: none;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.6);
  color: #fff;
  font-size: 11px;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.15s;
  display: flex;
  align-items: center;
  justify-content: center;
}

.candidate-tile:hover .candidate-expand {
  opacity: 1;
}

.candidates-empty {
  color: var(--text-color-secondary);
  font-size: 12px;
  padding: 8px 0;
}

.bc-dialog {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding-left: 8px;
  border-left: 2px solid var(--surface-border);
}

.bc-dialog-line {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.bc-dialog-line-row {
  display: flex;
  align-items: center;
  gap: 6px;
}

.bc-dialog-speaker {
  flex: 1;
}

.bc-dialog-voice {
  flex: 0 0 64px;
}

.bc-dialog-delivery {
  flex: 0 0 84px;
}

.bc-dialog-language {
  flex: 0 0 64px;
}

.bc-dialog-text {
  width: 100%;
}

.bc-dialog-add-btn {
  align-self: flex-start;
  padding: 3px 10px;
  border: 1px dashed var(--surface-border);
  border-radius: 5px;
  background: none;
  color: var(--text-color-secondary);
  font-size: 11px;
  cursor: pointer;
  transition:
    background 0.15s,
    color 0.15s;
}

.bc-dialog-add-btn:hover {
  background: var(--surface-hover);
  color: var(--text-color);
}
</style>
