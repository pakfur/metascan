<template>
  <div v-if="panel" class="panel-detail">
    <div class="pd-header">
      <h4>
        Panel {{ (panel.sort_order ?? 0) + 1 }} · {{ beat?.shot_size ?? '—' }} ·
        {{ beat?.angle ?? '—' }}
      </h4>
      <div class="pd-header-actions">
        <button class="pd-btn" :disabled="hasActiveJob || !beat" @click="reroll">Reroll</button>
        <button
          class="pd-btn"
          :disabled="hasActiveJob || store.synthesis.running || !beat"
          @click="resynth"
        >
          Re-synth
        </button>
      </div>
    </div>

    <div class="pd-body">
      <div class="pd-col pd-col-fields">
        <div class="pd-field">
          <label class="pd-label">Action</label>
          <input type="text" :value="actionVal" @change="commitAction" />
        </div>

        <!-- The fields below (shot size / angle / lens / subjects / prompt /
             candidates) belong to the selected beat now -- a beat is the
             image-generation unit since the shot->beat reorg. They apply to
             `store.selectedBeat`, not this panel; select a beat in the Beats
             list below to edit them. -->
        <div class="pd-field-row">
          <div class="pd-field">
            <label class="pd-label">Shot size</label>
            <select :value="shotSizeVal" :disabled="!beat" @change="commitShotSize">
              <option value="">—</option>
              <option v-for="s in SHOT_SIZES" :key="s" :value="s">{{ s }}</option>
            </select>
          </div>
          <div class="pd-field">
            <label class="pd-label">Angle</label>
            <select :value="angleVal" :disabled="!beat" @change="commitAngle">
              <option value="">—</option>
              <option v-for="a in ANGLES" :key="a" :value="a">{{ a }}</option>
            </select>
          </div>
          <div class="pd-field">
            <label class="pd-label">Lens</label>
            <select :value="lensVal" :disabled="!beat" @change="commitLens">
              <option value="">—</option>
              <option v-for="l in LENSES" :key="l" :value="l">{{ l }}</option>
            </select>
          </div>
        </div>

        <div class="pd-field">
          <label class="pd-label">Subjects</label>
          <template v-if="beat">
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
              <label v-for="s in store.tree?.subjects ?? []" :key="s.id" class="subject-check">
                <input
                  type="checkbox"
                  :checked="beat.subject_ids.includes(s.id)"
                  @change="toggleSubject(s.id)"
                />
                {{ s.name }}
              </label>
              <span v-if="(store.tree?.subjects.length ?? 0) === 0" class="pd-hint">
                No subjects defined yet.
              </span>
            </div>
          </template>
          <span v-else class="pd-hint">Select a beat to edit subjects.</span>
        </div>

        <div class="pd-field">
          <div class="pd-prompt-header">
            <label class="pd-label">Prompt</label>
            <span class="pd-prompt-status">{{ promptStatusLabel }}</span>
            <button
              v-if="beat?.prompt_locked === 1"
              type="button"
              class="pd-link-btn"
              @click="unlockPrompt"
            >
              Unlock
            </button>
          </div>
          <textarea :value="promptVal" rows="4" :disabled="!beat" @change="commitPrompt" />
        </div>

        <div class="pd-field">
          <label class="pd-label">Duration (s)</label>
          <input
            type="number"
            step="0.5"
            min="0.5"
            :value="durationVal"
            @change="commitDuration"
          />
        </div>

        <BeatsEditor :panel="panel" />
      </div>

      <div class="pd-col pd-col-candidates">
        <label class="pd-label">Candidates</label>
        <div class="candidates-row">
          <div
            v-for="(img, idx) in beat?.images ?? []"
            :key="img.id"
            class="candidate-tile"
            :class="{ selected: img.id === beat?.selected_image_id }"
            :title="candidateTooltip(img)"
            @click="onCandidateClick(img)"
          >
            <img :src="thumbnailUrl(img.file_path)" alt="" class="candidate-img" />
            <span v-if="img.id === beat?.selected_image_id" class="candidate-check">✓</span>
            <button
              type="button"
              class="candidate-expand"
              title="View full size"
              @click.stop="viewerIndex = idx"
            >
              <span class="pi pi-search-plus" />
            </button>
          </div>
          <div v-if="!beat || beat.images.length === 0" class="candidates-empty">
            {{ beat ? 'No candidates yet.' : 'Select a beat to see candidates.' }}
          </div>
        </div>
      </div>
    </div>
  </div>

  <MediaViewer
    v-if="viewerIndex !== null"
    :media-list="viewerMedia"
    :initial-index="viewerIndex"
    :allow-destructive="false"
    @close="viewerIndex = null"
  />
</template>

<script setup lang="ts">
import { computed, ref, watch, type Ref } from 'vue'
import { thumbnailUrl } from '../../api/client'
import { useStoryboardStore } from '../../stores/storyboard'
import { SHOT_SIZES, ANGLES, LENSES } from '../../types/storyboard'
import type { BeatImage } from '../../types/storyboard'
import type { Media } from '../../types/media'
import { isVideoPath } from '../../utils/path'
import MediaViewer from '../viewer/MediaViewer.vue'
import BeatsEditor from './BeatsEditor.vue'

const store = useStoryboardStore()
const panel = computed(() => store.selectedPanel)
// Shot-level fields (shot size / angle / lens / subjects / prompt /
// candidates) now live on the beat, not the panel -- see the shot->beat
// reorg. This tracks whichever beat is selected in the Beats list below.
const beat = computed(() => store.selectedBeat)

const hasActiveJob = computed(() =>
  panel.value ? store.panelJobState.has(panel.value.id) : false,
)

// Local editable copies of the "commit on change" text/select fields.
//
// Each field also carries a "last synced from server" snapshot (below) --
// commit* functions update local + snapshot together (see the `sync`
// helper), so a field with no pending edit always has local === snapshot.
// The watcher further down uses that equality to decide, on every
// server-driven refresh, whether it's safe to overwrite a field: adopt the
// new server value when local === snapshot (no pending edit), leave it
// alone otherwise (an uncommitted PATCH is in flight for that field).
// Without this, a synthesis/VLM pass that rewrites e.g. `prompt` on the
// CURRENTLY SELECTED beat would never reach the textarea, and a later blur
// would PATCH the stale (often empty) local value back over the server's
// synthesized one.
const actionVal = ref('')
const promptVal = ref('')
const shotSizeVal = ref('')
const angleVal = ref('')
const lensVal = ref('')
const durationVal = ref('0')

const actionSnap = ref('')
const promptSnap = ref('')
const shotSizeSnap = ref('')
const angleSnap = ref('')
const lensSnap = ref('')
const durationSnap = ref('0')

function syncField(local: Ref<string>, snap: Ref<string>, serverVal: string): void {
  if (local.value === snap.value) {
    local.value = serverVal
    snap.value = serverVal
  }
}

// Resyncs on a panel/beat switch (by id) AND on any server-side rewrite of
// the CURRENTLY selected panel/beat (detected via updated_at -- bumped on
// every successful PATCH, including synthesis/VLM writes and edits from
// another tab). Subject membership/order isn't tracked here: the template
// reads `beat.subject_ids` directly rather than through a local cached
// copy, so it's always current and needs no resync of its own.
watch(
  () => [panel.value?.id, panel.value?.updated_at, beat.value?.id, beat.value?.updated_at],
  () => {
    const p = panel.value
    if (p) {
      syncField(actionVal, actionSnap, p.action ?? '')
      syncField(durationVal, durationSnap, String(p.duration_s))
    }
    const b = beat.value
    syncField(promptVal, promptSnap, b?.prompt ?? '')
    syncField(shotSizeVal, shotSizeSnap, b?.shot_size ?? '')
    syncField(angleVal, angleSnap, b?.angle ?? '')
    syncField(lensVal, lensSnap, b?.lens ?? '')
  },
  { immediate: true },
)

function subjectName(id: number): string {
  return store.subjectsById.get(id)?.name ?? `#${id}`
}

function reroll(): void {
  if (!beat.value) return
  void store.generate([beat.value.id])
}

function resynth(): void {
  if (!beat.value) return
  void store.synthesize([beat.value.id], true)
}

// Every commit* handler updates its local ref AND snapshot together
// (before the optimistic patch resolves) so the field is never mistaken
// for "someone else's pending edit" by the resync watcher above once the
// round trip's updated_at bump comes back through.
function commitAction(e: Event): void {
  const val = (e.target as HTMLInputElement).value
  actionVal.value = val
  actionSnap.value = val
  if (!panel.value || val === panel.value.action) return
  void store.patchPanelFields(panel.value.id, { action: val })
}

function commitPrompt(e: Event): void {
  const val = (e.target as HTMLTextAreaElement).value
  promptVal.value = val
  promptSnap.value = val
  if (!beat.value) return
  if (val === (beat.value.prompt ?? '')) return
  void store.patchBeatFields(beat.value.id, { prompt: val })
}

function unlockPrompt(): void {
  if (!beat.value) return
  void store.patchBeatFields(beat.value.id, { prompt_locked: 0 })
}

function commitShotSize(e: Event): void {
  const val = (e.target as HTMLSelectElement).value
  shotSizeVal.value = val
  shotSizeSnap.value = val
  if (!beat.value) return
  const next = val === '' ? null : val
  if (next === beat.value.shot_size) return
  void store.patchBeatFields(beat.value.id, { shot_size: next })
}

function commitAngle(e: Event): void {
  const val = (e.target as HTMLSelectElement).value
  angleVal.value = val
  angleSnap.value = val
  if (!beat.value) return
  const next = val === '' ? null : val
  if (next === beat.value.angle) return
  void store.patchBeatFields(beat.value.id, { angle: next })
}

function commitLens(e: Event): void {
  const val = (e.target as HTMLSelectElement).value
  lensVal.value = val
  lensSnap.value = val
  if (!beat.value) return
  const next = val === '' ? null : val
  if (next === beat.value.lens) return
  void store.patchBeatFields(beat.value.id, { lens: next })
}

function commitDuration(e: Event): void {
  const val = (e.target as HTMLInputElement).value
  durationVal.value = val
  durationSnap.value = val
  if (!panel.value) return
  const next = Math.max(0.5, Number(val) || panel.value.duration_s)
  if (next === panel.value.duration_s) return
  void store.patchPanelFields(panel.value.id, { duration_s: next })
}

function toggleSubject(id: number): void {
  if (!beat.value) return
  const current = [...beat.value.subject_ids]
  const idx = current.indexOf(id)
  if (idx >= 0) current.splice(idx, 1)
  else current.push(id)
  void store.patchBeatFields(beat.value.id, { subject_ids: current })
}

function promoteSubject(id: number): void {
  if (!beat.value) return
  const current = beat.value.subject_ids.filter((x: number) => x !== id)
  current.unshift(id)
  void store.patchBeatFields(beat.value.id, { subject_ids: current })
}

const promptStatusLabel = computed(() => {
  const b = beat.value
  if (!b) return '—'
  if (b.prompt_locked === 1) return '🔒 edited'
  if (b.prompt_source === 'brief') return 'brief fallback'
  if (b.prompt_source === 'llm') return 'synthesized'
  return '—'
})

function candidateTooltip(img: BeatImage): string {
  return `seed ${img.seed ?? '—'} · variant ${img.variant_index}`
}

function onCandidateClick(img: BeatImage): void {
  if (!beat.value) return
  const next = beat.value.selected_image_id === img.id ? null : img.id
  void store.selectImage(beat.value.id, next)
}

const viewerIndex = ref<number | null>(null)
const viewerMedia = computed<Media[]>(() =>
  (store.selectedBeat?.images ?? []).map(
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
</script>

<style scoped>
.panel-detail {
  flex-shrink: 0;
  max-height: 44vh;
  overflow-y: auto;
  border-top: 1px solid var(--surface-border);
  background: var(--surface-card);
  padding: 12px 20px 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.pd-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.pd-header h4 {
  margin: 0;
  font-size: 14px;
  color: var(--text-color);
}

.pd-header-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}

.pd-btn {
  padding: 5px 12px;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-ground);
  color: var(--text-color);
  font-size: 12px;
  cursor: pointer;
}

.pd-btn:hover:not(:disabled) {
  background: var(--surface-hover);
}

.pd-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.pd-body {
  display: grid;
  grid-template-columns: minmax(280px, 1fr) minmax(220px, 1fr);
  gap: 20px;
  align-items: start;
}

.pd-col {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-width: 0;
}

.pd-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.pd-field-row {
  display: flex;
  gap: 10px;
}

.pd-field-row .pd-field {
  flex: 1;
  min-width: 0;
}

.pd-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-color-secondary);
  text-transform: uppercase;
  letter-spacing: 0.4px;
}

.pd-hint {
  font-size: 11px;
  color: var(--text-color-secondary);
}

input[type='text'],
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

input[type='text']:focus,
select:focus,
textarea:focus {
  outline: none;
  border-color: var(--primary-color);
}

textarea {
  resize: vertical;
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

.pd-prompt-header {
  display: flex;
  align-items: center;
  gap: 10px;
}

.pd-prompt-status {
  font-size: 11px;
  color: var(--text-color-secondary);
  flex: 1;
}

.pd-link-btn {
  background: none;
  border: none;
  padding: 0;
  color: var(--primary-color);
  cursor: pointer;
  font-size: 11px;
  text-decoration: underline;
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
</style>
