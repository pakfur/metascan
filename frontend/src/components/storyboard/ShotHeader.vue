<script setup lang="ts">
import { computed, ref, watch, type Ref } from 'vue'
import { ApiError, thumbnailUrl } from '../../api/client'
import { useStoryboardStore } from '../../stores/storyboard'
import type { LoraEntry, Panel, PanelVideo, Scene } from '../../types/storyboard'
import type { Media } from '../../types/media'
import PacingStrip from './PacingStrip.vue'
import LoraListEditor from './LoraListEditor.vue'
import TextEditPopup from './TextEditPopup.vue'
import VideoPromptDialog from './VideoPromptDialog.vue'
import ShotScriptDialog from './ShotScriptDialog.vue'
import MediaViewer from '../viewer/MediaViewer.vue'

const props = defineProps<{ panel: Panel; scene: Scene; index: number }>()
const emit = defineEmits<{ (e: 'select-beat', beatId: number): void }>()
const store = useStoryboardStore()

// -- ported from PanelDetail.vue --------------------------------------

// Local editable copy of the "commit on change" action field, paired with a
// "last synced from server" snapshot -- commitAction updates local + snapshot
// together, so a field with no pending edit always has local === snapshot.
// The watcher below uses that equality to decide, on every server-driven
// refresh, whether it's safe to overwrite the field: adopt the new server
// value when local === snapshot (no pending edit), leave it alone otherwise
// (an uncommitted PATCH is in flight for that field).
const actionVal = ref('')
const actionSnap = ref('')
const subtextVal = ref('')
const subtextSnap = ref('')

function syncField(local: Ref<string>, snap: Ref<string>, serverVal: string): void {
  if (local.value === snap.value) {
    local.value = serverVal
    snap.value = serverVal
  }
}

// Resyncs on a panel switch (by id) AND on any server-side rewrite of the
// currently displayed panel (detected via updated_at -- bumped on every
// successful PATCH, including edits from another tab).
watch(
  () => [props.panel.id, props.panel.updated_at],
  () => {
    syncField(actionVal, actionSnap, props.panel.action ?? '')
    syncField(subtextVal, subtextSnap, props.panel.subtext ?? '')
  },
  { immediate: true },
)

function commitActionVal(val: string): void {
  actionVal.value = val
  actionSnap.value = val
  if (val === props.panel.action) return
  void store.patchPanelFields(props.panel.id, { action: val })
}

function commitAction(e: Event): void {
  commitActionVal((e.target as HTMLInputElement).value)
}

function commitSubtextVal(val: string): void {
  subtextVal.value = val
  subtextSnap.value = val
  const next = val.trim() || null
  if (next === (props.panel.subtext ?? null)) return
  void store.patchPanelFields(props.panel.id, { subtext: next })
}

function commitSubtext(e: Event): void {
  commitSubtextVal((e.target as HTMLInputElement).value)
}

function toggleTurn(): void {
  void store.patchPanelFields(props.panel.id, {
    is_turn: props.panel.is_turn ? 0 : 1,
  })
}

// LoraListEditor rows are fully controlled + commit-on-change, so unlike
// the text field above there is no local copy to snapshot -- every change
// event already carries the complete next list.
function commitVideoLoras(entries: LoraEntry[]): void {
  void store.patchPanelFields(props.panel.id, { video_loras: entries })
}

// -- ported from BeatsEditor.vue (re-beat flow) ------------------------

// `check_compose_gates` 409s the beats stage (confirm_required) whenever any
// target beat already has generated images or a locked prompt -- recomposing
// would destroy them.
const confirmPending = ref(false)
const rebeatError = ref<string | null>(null)

async function rebeat(confirm = false): Promise<void> {
  rebeatError.value = null
  confirmPending.value = false
  try {
    await store.composeStory({ stages: ['beats'], panel_ids: [props.panel.id], confirm })
  } catch (e: unknown) {
    if (
      e instanceof ApiError &&
      e.status === 409 &&
      (e.detail as { code?: string } | undefined)?.code === 'confirm_required'
    ) {
      confirmPending.value = true
    } else {
      rebeatError.value = e instanceof Error ? e.message : String(e)
    }
  }
}

// -- ported from PanelVideos.vue ----------------------------------------

function tooltip(v: PanelVideo): string {
  return `seed ${v.seed ?? '—'} · take ${v.variant_index + 1}`
}

// Delete is total: panel_videos row, media row, and the file to the OS
// trash -- hence the confirm.
async function onDelete(video: PanelVideo): Promise<void> {
  const name = video.file_path.split(/[\\/]/).pop() ?? video.file_path
  if (
    !confirm(
      `Delete take ${video.variant_index + 1} (${name})?\nThis removes it from the library and moves the file to the trash.`,
    )
  ) {
    return
  }
  await store.removePanelVideo(video.id)
}

const viewerIndex = ref<number | null>(null)
const viewerMedia = computed<Media[]>(() =>
  props.panel.videos.map(
    (v) =>
      ({
        file_path: v.file_path,
        is_favorite: false,
        is_video: true,
        playback_speed: null,
        width: 0,
        height: 0,
        file_size: 0,
        frame_rate: null,
        duration: null,
      }) as Media,
  ),
)

// -- new for the redesign -------------------------------------------------

const videoPromptOpen = ref(false)
const scriptOpen = ref(false)

const sourceLabel = computed(() =>
  props.panel.video_prompt_source === 'user'
    ? 'user edited'
    : (props.panel.video_prompt_source ?? 'not compiled'),
)
const lintCount = computed(() => props.panel.video_prompt_warnings.length)
const anchorRelevant = computed(
  () => store.tree?.video_mode === 'i2va' || store.tree?.video_mode === 'fl2va',
)
const recompileSuggested = computed(
  () => anchorRelevant.value && props.panel.video_anchor !== props.panel.video_compiled_anchor,
)
// SQLite's datetime('now') writes 'YYYY-MM-DD HH:MM:SS'; normalize any 'T'
// separator before the plain string compare (both timestamps are UTC).
function normTs(s: string): string {
  return s.replace('T', ' ')
}
const beatsChangedSinceCompile = computed(() => {
  const compiledAt = props.panel.video_compiled_at
  if (!compiledAt) return false
  const compiledNorm = normTs(compiledAt)
  return props.panel.beats.some((b) => normTs(b.updated_at) > compiledNorm)
})
const videoReady = computed(() => !!store.tree?.video_target && !!store.tree?.video_preset_id)

function compile(): void {
  void store.compileVideo([props.panel.id])
}

// -- downstream-dependency prompts (utils/storyboardDeps.ts) -----------
//
// The store records a prompt after any successful PATCH of a field the
// beats stage (shot action/subtext) or the H3 compiler (beat fields) reads.
// Offer the recompute inline; "Re-beat" reuses rebeat() so its destructive
// 409 confirm still applies.
const beatsPrompt = computed(() => store.downstreamPromptsFor('beats', props.panel.id)[0] ?? null)
const compilePrompt = computed(
  () => store.downstreamPromptsFor('compile', props.panel.id)[0] ?? null,
)
const FIELD_LABEL: Record<string, string> = {
  action: 'Action',
  subtext: 'Subtext',
  is_turn: 'Turn',
}
function fieldsLabel(fields: string[]): string {
  return fields.map((f) => FIELD_LABEL[f] ?? f.replace(/_/g, ' ')).join(', ')
}
function acceptBeatsPrompt(): void {
  void rebeat()
}
function acceptCompilePrompt(): void {
  compile()
}
async function renderVideo(): Promise<void> {
  const res = await store.generateVideo([props.panel.id])
  if (res && res.skipped.length > 0) {
    store.error = res.skipped.map((s) => `panel ${s.panel_id}: ${s.error}`).join('\n')
  }
}
</script>

<template>
  <div class="sh-root">
    <div class="sh-row1">
      <span class="sh-breadcrumb">{{ scene.name }} ›</span>
      <h3 class="sh-title">Shot {{ index + 1 }}</h3>
      <span class="sh-meta">{{ panel.beats.length }} beats</span>
      <div class="sh-row1-actions">
        <button type="button" class="sh-btn" :disabled="store.story.running" @click="rebeat()">
          Re-beat shot
        </button>
      </div>
    </div>

    <p v-if="confirmPending" class="sh-confirm">
      Beats have generated images or locked prompts — recomposing deletes the images (moved to
      the OS trash). Continue?
      <span class="sh-confirm-actions">
        <button
          type="button"
          class="sh-btn sh-btn-danger"
          :disabled="store.story.running"
          @click="rebeat(true)"
        >
          Continue
        </button>
        <button
          type="button"
          class="sh-btn"
          :disabled="store.story.running"
          @click="confirmPending = false"
        >
          Cancel
        </button>
      </span>
    </p>
    <p v-else-if="rebeatError" class="sh-error">{{ rebeatError }}</p>

    <p v-if="beatsPrompt && !confirmPending" class="sh-downstream">
      {{ fieldsLabel(beatsPrompt.fields) }} changed — recalculate the beats for this shot?
      <span class="sh-confirm-actions">
        <button
          type="button"
          class="sh-btn sh-btn--primary"
          :disabled="store.story.running"
          @click="acceptBeatsPrompt"
        >
          Re-beat shot
        </button>
        <button type="button" class="sh-btn" @click="store.dismissDownstream(beatsPrompt.key)">
          Not now
        </button>
      </span>
    </p>
    <p v-if="compilePrompt" class="sh-downstream">
      Beat {{ fieldsLabel(compilePrompt.fields) }} changed — recompile the video prompt?
      <span class="sh-confirm-actions">
        <button
          type="button"
          class="sh-btn sh-btn--primary"
          :disabled="store.compile.running"
          @click="acceptCompilePrompt"
        >
          Compile
        </button>
        <button type="button" class="sh-btn" @click="store.dismissDownstream(compilePrompt.key)">
          Not now
        </button>
      </span>
    </p>

    <PacingStrip :panel="panel" @select-beat="emit('select-beat', $event)" />

    <div class="sh-row3">
      <div class="sh-field sh-action-field">
        <label class="sh-label">Shot action</label>
        <div class="sh-action-row">
          <TextEditPopup
            class="sh-tep-grow"
            title="Shot action"
            :value="actionVal"
            @save="commitActionVal"
          >
            <input type="text" :value="actionVal" @change="commitAction" />
          </TextEditPopup>
          <button
            type="button"
            class="sh-turn"
            :class="{ on: panel.is_turn === 1 }"
            title="Mark as the story's turn"
            @click="toggleTurn"
          >
            ★ Turn
          </button>
        </div>
      </div>

      <div class="sh-video-group">
        <button
          v-if="store.tree?.video_target"
          type="button"
          class="sh-btn"
          @click="videoPromptOpen = true"
        >
          <i class="pi pi-file" /> Video prompt
        </button>
        <button type="button" class="sh-btn" @click="scriptOpen = true">Shot script</button>
        <template v-if="store.tree?.video_target">
          <span class="sh-status">{{ sourceLabel }}</span>
          <span
            v-if="panel.video_prompt_locked === 1"
            class="sh-lock"
            title="Locked against the next compile"
            >🔒</span
          >
          <span v-if="lintCount > 0" class="sh-chip sh-chip--warn">
            {{ lintCount }} lint {{ lintCount === 1 ? 'warning' : 'warnings' }}
          </span>
          <span
            v-if="recompileSuggested"
            class="sh-chip sh-chip--warn"
            title="Anchor changed since the last compile"
          >
            recompile suggested
          </span>
          <span
            v-if="beatsChangedSinceCompile"
            class="sh-chip sh-chip--warn"
            title="Beats changed since the last compile"
          >
            beats changed
          </span>
          <button type="button" class="sh-btn" :disabled="store.compile.running" @click="compile">
            Compile
          </button>
          <button
            type="button"
            class="sh-btn sh-btn--primary"
            :disabled="!panel.video_prompt || !videoReady"
            @click="renderVideo"
          >
            <i class="pi pi-play" /> Render video
          </button>
        </template>
      </div>
    </div>

    <label class="sh-field">
      <span class="sh-label">Subtext</span>
      <TextEditPopup title="Subtext" :value="subtextVal" @save="commitSubtextVal">
        <input
          type="text"
          :value="subtextVal"
          placeholder="what the shot means but doesn't show"
          @change="commitSubtext"
        />
      </TextEditPopup>
    </label>

    <div class="sh-row4">
      <div class="sh-lora-wrap">
        <LoraListEditor label="Video LoRAs" :entries="panel.video_loras" @change="commitVideoLoras" />
      </div>

      <div class="sh-takes">
        <div class="sh-takes-head">
          <label class="sh-label">Takes ({{ panel.videos.length }})</label>
          <span v-if="panel.videos.length > 0" class="sh-takes-hint">
            newest last · double-click to play
          </span>
        </div>
        <div v-if="panel.videos.length > 0" class="sh-takes-row">
          <div
            v-for="(video, idx) in panel.videos"
            :key="video.id"
            class="sh-tile"
            :title="tooltip(video)"
            @dblclick="viewerIndex = idx"
          >
            <img :src="thumbnailUrl(video.file_path)" alt="" class="sh-tile-img" />
            <span class="sh-tile-play" title="Play" @click.stop="viewerIndex = idx">▶</span>
            <button type="button" class="sh-tile-delete" title="Delete take" @click.stop="onDelete(video)">
              ×
            </button>
          </div>
        </div>
        <div v-else class="sh-takes-empty">No takes rendered yet.</div>
      </div>
    </div>
  </div>

  <VideoPromptDialog
    v-if="videoPromptOpen"
    :panel="panel"
    :index="index"
    @close="videoPromptOpen = false"
  />
  <ShotScriptDialog
    v-if="scriptOpen"
    :panel="panel"
    :scene="scene"
    :index="index"
    @close="scriptOpen = false"
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
.sh-root {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--surface-border);
}

/* Row 1 */
.sh-row1 {
  display: flex;
  align-items: baseline;
  gap: 10px;
}

.sh-breadcrumb {
  font-size: 11px;
  color: var(--text-color-secondary);
}

.sh-title {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
}

.sh-meta {
  font-size: 11px;
  color: var(--text-color-secondary);
}

.sh-row1-actions {
  margin-left: auto;
  display: flex;
  gap: 8px;
}

/* Secondary / primary buttons shared across all rows */
.sh-btn {
  padding: 5px 12px;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-ground);
  color: var(--text-color);
  font-size: 12px;
  cursor: pointer;
  transition: background 0.15s;
}

.sh-btn:hover:not(:disabled) {
  background: var(--surface-hover);
}

.sh-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.sh-btn--primary {
  background: var(--primary-color);
  border-color: var(--primary-color);
  color: #fff;
}

.sh-btn-danger {
  border-color: var(--danger-color, #e53e3e);
  color: var(--danger-color, #e53e3e);
}

.sh-btn-danger:hover:not(:disabled) {
  background: color-mix(in srgb, var(--danger-color, #e53e3e) 12%, transparent);
}

/* Re-beat confirm banner (ported from BeatsEditor.vue) */
.sh-confirm {
  margin: 0;
  padding: 8px 10px;
  border-radius: 6px;
  font-size: 12px;
  color: var(--text-color);
  background: color-mix(in srgb, var(--danger-color, #e53e3e) 12%, transparent);
  border: 1px solid color-mix(in srgb, var(--danger-color, #e53e3e) 40%, transparent);
}

.sh-confirm-actions {
  display: inline-flex;
  gap: 8px;
  margin-left: 10px;
}

.sh-error {
  margin: 0;
  font-size: 12px;
  color: var(--danger-color, #e53e3e);
}

/* Downstream-dependency prompt: same shape as the confirm banner, but
   informational (primary tint) rather than destructive. */
.sh-downstream {
  margin: 0;
  padding: 8px 10px;
  border-radius: 6px;
  font-size: 12px;
  color: var(--text-color);
  background: color-mix(in srgb, var(--primary-color) 10%, transparent);
  border: 1px solid color-mix(in srgb, var(--primary-color) 40%, transparent);
}

/* Row 3 */
.sh-row3 {
  display: flex;
  gap: 16px;
  align-items: flex-end;
}

.sh-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.sh-action-field {
  flex: 1;
  min-width: 220px;
}

.sh-action-row {
  display: flex;
  gap: 8px;
  align-items: center;
}

/* TextEditPopup root takes over the input's old flex-item role. */
.sh-tep-grow {
  flex: 1;
  min-width: 0;
}

.sh-turn {
  flex-shrink: 0;
  padding: 5px 10px;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-ground);
  color: var(--text-color-secondary);
  font-size: 12px;
  cursor: pointer;
  transition:
    background 0.15s,
    color 0.15s,
    border-color 0.15s;
}

.sh-turn:hover {
  background: var(--surface-hover);
}

.sh-turn.on {
  color: var(--warn, #ffb300);
  border-color: var(--warn, #ffb300);
  background: color-mix(in srgb, var(--warn, #ffb300) 14%, transparent);
}

.sh-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-color-secondary);
  text-transform: uppercase;
  letter-spacing: 0.4px;
}

input[type='text'] {
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

input[type='text']:focus {
  outline: none;
  border-color: var(--primary-color);
}

.sh-video-group {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 10px;
}

.sh-status {
  font-size: 11px;
  color: var(--text-color-secondary);
}

.sh-lock {
  font-size: 11px;
}

.sh-chip {
  font-size: 12px;
  padding: 4px 10px;
  border-radius: 999px;
}

.sh-chip--warn {
  color: var(--warn);
  background: color-mix(in srgb, var(--warn) 14%, transparent);
}

/* Row 4 */
.sh-row4 {
  display: flex;
  gap: 16px;
  align-items: flex-start;
}

.sh-lora-wrap {
  flex: 1;
  min-width: 236px;
}

.sh-takes {
  flex: 0 0 374px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.sh-takes-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.sh-takes-hint {
  font-size: 11px;
  color: var(--text-color-secondary);
}

.sh-takes-row {
  display: flex;
  gap: 10px;
  overflow-x: auto;
  padding-bottom: 4px;
}

.sh-tile {
  position: relative;
  flex: 0 0 auto;
  width: 176px;
  height: 99px;
  border-radius: 6px;
  overflow: hidden;
  background: var(--surface-ground);
  cursor: pointer;
}

.sh-tile-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.sh-tile-play {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  color: #fff;
  background: rgba(0, 0, 0, 0.25);
  opacity: 0;
  transition: opacity 0.15s;
}

.sh-tile:hover .sh-tile-play {
  opacity: 1;
}

.sh-tile-delete {
  position: absolute;
  top: 4px;
  right: 4px;
  width: 20px;
  height: 20px;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.6);
  color: #fff;
  font-size: 13px;
  line-height: 18px;
  cursor: pointer;
  opacity: 0;
  transition:
    opacity 0.15s,
    background 0.15s;
  z-index: 1;
}

.sh-tile:hover .sh-tile-delete {
  opacity: 1;
}

.sh-tile-delete:hover {
  background: color-mix(in srgb, var(--danger-color, #e53e3e) 75%, black);
}

.sh-takes-empty {
  height: 99px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px dashed var(--surface-border);
  border-radius: 6px;
  font-size: 12px;
  color: var(--text-color-secondary);
}
</style>
