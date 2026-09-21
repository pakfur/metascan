<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { Media } from '../../types/media'
import { i2vDims, i2vVideoDetails, type I2vFix, type I2vVideo } from '../../types/i2v'
import { useI2vStore } from '../../stores/i2v'
import { generatePrompt, generateVideo, deleteI2vVideo, lintI2vPrompt } from '../../api/i2v'
import { thumbnailUrl } from '../../api/client'
import { useWebSocket } from '../../composables/useWebSocket'
import { useToast } from '../../composables/useToast'
import { useMediaStore } from '../../stores/media'
import MediaViewer from '../viewer/MediaViewer.vue'
import LoraListEditor from '../storyboard/LoraListEditor.vue'
import type { LoraEntry } from '../../types/storyboard'

const props = defineProps<{ media: Media }>()
const emit = defineEmits<{ close: [] }>()

const store = useI2vStore()
const mediaStore = useMediaStore()
const toast = useToast()

const idea = ref('')
const prompt = ref('')
const warnings = ref<string[]>([])
const durationS = ref(6)
const quality = ref<'fast' | 'quality'>('fast')
const megapixels = ref(0.75)
const steps = ref(25)
const cancelling = ref(false)
const seed = ref(randomSeed())
const loras = ref<LoraEntry[]>([])
const expanding = ref(false)
const submitting = ref(false)
const viewerIndex = ref<number | null>(null)

function randomSeed(): number {
  return Math.floor(Math.random() * 2 ** 31)
}

onMounted(async () => {
  await store.open(props.media)
  const cfg = store.config
  if (cfg) {
    durationS.value = cfg.default_duration
    quality.value = cfg.default_quality
    megapixels.value = cfg.default_megapixels
    steps.value = cfg.default_steps
  }
})

function close() {
  store.close()
  emit('close')
}

// useWebSocket(channel, handler) calls handler(event, data) per message on
// that channel — see composables/useWebSocket.ts.
useWebSocket('i2v', (event, data) => {
  store.handleI2vEvent(event, data)
  if (event === 'i2v_videos_changed') void mediaStore.loadAllMedia()
})
useWebSocket('comfy', (event, data) => {
  store.handleComfyEvent(event, data)
})

const durations = computed(() => store.config?.durations ?? [6, 10, 15, 20])
const megapixelOptions = computed(() => store.config?.megapixels ?? [0.25, 0.5, 0.75, 1.0])
const stepOptions = computed(() => store.config?.steps ?? [20, 25, 30, 35, 40])
// Steps drive the High quality preset only (Fast is a step-distilled
// build), and only when that preset's workflow binds MS_STEPS.
const stepsSupported = computed(() => store.config?.quality_steps_supported ?? true)
const stepsEnabled = computed(() => quality.value === 'quality' && stepsSupported.value)
const stepsHint = computed(() => {
  if (quality.value !== 'quality') return 'High quality only'
  if (!stepsSupported.value) return 'preset has no MS_STEPS node'
  return ''
})
// Preview only; the server recomputes from the source's real dimensions.
const outputDims = computed(() =>
  i2vDims(props.media.width ?? 0, props.media.height ?? 0, megapixels.value),
)

// ---- live lint + "Apply fixes" ---------------------------------------------
// The prompt box is linted as it changes (generated text and hand edits
// alike). Some findings come with a rewrite the server can do without a
// model call -- natural camera phrasing, quoted speech. They are only
// OFFERED: Generate sends exactly what is in the box, so nothing is ever
// rewritten until the user presses Apply fixes.
const fixes = ref<I2vFix[]>([])
const fixedPrompt = ref<string | null>(null)
// The exact text the current fixes were computed for. Apply is disabled
// the moment the box diverges from it, so a stale rewrite can never
// clobber newer typing.
const lintedFor = ref<string | null>(null)
let lintTimer: ReturnType<typeof setTimeout> | null = null
let lintSeq = 0

async function runLint() {
  const text = prompt.value
  const seq = ++lintSeq
  if (!text.trim()) {
    warnings.value = []
    fixes.value = []
    fixedPrompt.value = null
    lintedFor.value = null
    return
  }
  try {
    const res = await lintI2vPrompt({ prompt: text, duration_s: durationS.value })
    if (seq !== lintSeq) return // a newer lint is in flight
    warnings.value = res.warnings
    fixes.value = res.fixes
    fixedPrompt.value = res.fixed_prompt
    lintedFor.value = text
  } catch {
    // Advisory only: a failed lint must never get in the way of editing.
    if (seq === lintSeq) {
      fixes.value = []
      fixedPrompt.value = null
      lintedFor.value = null
    }
  }
}

watch([prompt, durationS], () => {
  if (lintTimer) clearTimeout(lintTimer)
  lintTimer = setTimeout(runLint, 400)
})
onBeforeUnmount(() => {
  if (lintTimer) clearTimeout(lintTimer)
})

const canApplyFixes = computed(
  () => fixes.value.length > 0 && fixedPrompt.value !== null && lintedFor.value === prompt.value,
)

// Warnings that have a fix are shown with their before/after below, so
// they are left out of the plain list rather than appearing twice.
const plainWarnings = computed(() => {
  const fixable = new Set(fixes.value.map((f) => f.message))
  return warnings.value.filter((w) => !fixable.has(w))
})

function onApplyFixes() {
  if (!canApplyFixes.value || fixedPrompt.value === null) return
  const count = fixes.value.length
  prompt.value = fixedPrompt.value // the watcher re-lints the result
  toast.show(`Applied ${count} fix${count === 1 ? '' : 'es'}`, 'success')
}

async function onExpandPrompt() {
  expanding.value = true
  try {
    const res = await generatePrompt({
      source_path: props.media.file_path,
      idea: idea.value,
      duration_s: durationS.value,
    })
    prompt.value = res.prompt
    warnings.value = res.warnings
  } catch (e) {
    toast.show(e instanceof Error ? e.message : String(e), 'warn')
  } finally {
    expanding.value = false
  }
}

// Everything that determines the rendered clip, as one comparable string.
// Steps count only when they would actually be sent. Seed, quality, steps
// and prompt are the ones a user normally varies between takes; duration,
// size and LoRAs are included too because changing any of them is also a
// genuinely different render and must not trigger the prompt below.
function requestSignature(): string {
  return JSON.stringify({
    prompt: prompt.value,
    seed: seed.value,
    quality: quality.value,
    steps: stepsEnabled.value ? steps.value : null,
    duration: durationS.value,
    megapixels: megapixels.value,
    loras: loras.value.map((l) => [l.name, l.strength]),
  })
}

// Signature of the last SUCCESSFUL submit from this dialog instance.
// Component-local on purpose: earlier sessions/runs are not considered.
const lastSubmitted = ref<string | null>(null)

async function onGenerate() {
  if (!prompt.value.trim()) {
    toast.show('Write or generate a prompt first', 'warn')
    return
  }
  const signature = requestSignature()
  if (
    signature === lastSubmitted.value &&
    !confirm(
      'Nothing has changed since your last Generate — same seed, quality, ' +
        'steps and prompt. This will render an identical video.\n\n' +
        'Generate it again anyway? (Cancel, then 🎲 for a new seed.)',
    )
  ) {
    return
  }
  submitting.value = true
  try {
    const res = await generateVideo({
      source_path: props.media.file_path,
      prompt: prompt.value,
      duration_s: durationS.value,
      quality: quality.value,
      seed: seed.value,
      megapixels: megapixels.value,
      loras: loras.value,
      idea: idea.value,
      ...(stepsEnabled.value ? { steps: steps.value } : {}),
    })
    warnings.value = res.warnings
    lastSubmitted.value = signature
    store.trackJob(res.job_id)
    toast.show('Video job queued', 'success')
  } catch (e) {
    toast.show(e instanceof Error ? e.message : String(e), 'warn')
  } finally {
    submitting.value = false
  }
}

// Footer Cancel: stops every queued/running job for this image. Each job
// tile also carries its own ✕ for cancelling just that one.
async function onCancelAll() {
  const count = store.activeJobIds.length
  if (!count) return
  cancelling.value = true
  try {
    await store.cancelAllJobs()
    toast.show(count > 1 ? `Cancelled ${count} video jobs` : 'Video job cancelled', 'success')
  } catch (e) {
    toast.show(e instanceof Error ? e.message : String(e), 'warn')
  } finally {
    cancelling.value = false
  }
}

async function onCancelJob(jobId: number) {
  try {
    await store.cancelJob(jobId)
  } catch (e) {
    toast.show(e instanceof Error ? e.message : String(e), 'warn')
  }
}

async function onDeleteVideo(v: I2vVideo) {
  if (!confirm('Delete this video? The file goes to the OS trash.')) return
  try {
    await deleteI2vVideo(v.id)
    await store.refreshVideos()
    void mediaStore.loadAllMedia()
  } catch (e) {
    toast.show(e instanceof Error ? e.message : String(e), 'warn')
  }
}

async function onToggleFavorite(v: I2vVideo) {
  try {
    await store.toggleFavorite(v)
  } catch (e) {
    toast.show(e instanceof Error ? e.message : String(e), 'warn')
  }
}

// Synthesized Media list for the read-only viewer (ShotHeader precedent).
const viewerMedia = computed<Media[]>(() =>
  store.videos.map((v) => ({
    ...props.media,
    file_path: v.file_path,
    file_name: v.file_path.split(/[\\/]/).pop() ?? v.file_path,
    is_video: true,
    is_favorite: v.is_favorite,
  })),
)

// Details label under each clip: quality (+ steps), clip length / render
// time, and when it was generated. Index-aligned with store.videos.
const videoDetails = computed(() => store.videos.map(i2vVideoDetails))

function jobLabel(chip: {
  state: string
  value?: number
  max?: number
  cancelling?: boolean
}): string {
  if (chip.cancelling) return 'cancelling…'
  if (chip.state === 'running' && chip.value != null && chip.max) {
    return `${Math.round((chip.value / chip.max) * 100)}%`
  }
  return chip.state
}
</script>

<template>
  <!-- No @click.self close, deliberately: a stray click on the backdrop
       must not throw away a written prompt or hide running jobs. The
       header ✕ is the only way out. -->
  <div class="dialog-overlay">
    <div class="i2v-card">
      <header class="i2v-header">
        <h3>Image to Video</h3>
        <button class="icon-btn" title="Close" @click="close()">✕</button>
      </header>

      <div class="i2v-top">
        <img class="i2v-source" :src="thumbnailUrl(media.file_path)" alt="" />
        <div class="i2v-controls">
          <label class="fld">
            <span>Idea</span>
            <textarea
              v-model="idea"
              rows="3"
              placeholder="Short idea for the video (optional)"
            />
          </label>
          <button class="btn" :disabled="expanding" @click="onExpandPrompt">
            {{ expanding ? 'Generating prompt…' : 'Generate prompt' }}
          </button>
          <div class="params">
            <label class="fld">
              <span>Duration</span>
              <select v-model.number="durationS">
                <option v-for="d in durations" :key="d" :value="d">{{ d }}s</option>
              </select>
            </label>
            <label class="fld">
              <span>Quality</span>
              <select v-model="quality">
                <option value="fast">Fast (turbo)</option>
                <option value="quality">High quality</option>
              </select>
            </label>
            <label class="fld" :class="{ 'fld-off': !stepsEnabled }" :title="stepsHint">
              <span>Steps</span>
              <select v-model.number="steps" :disabled="!stepsEnabled">
                <option v-for="n in stepOptions" :key="n" :value="n">{{ n }}</option>
              </select>
              <small v-if="stepsHint" class="dims-hint">{{ stepsHint }}</small>
            </label>
            <label class="fld">
              <span>Size</span>
              <select v-model.number="megapixels">
                <option v-for="m in megapixelOptions" :key="m" :value="m">
                  {{ m }} MP
                </option>
              </select>
              <small class="dims-hint">
                {{ outputDims ? `${outputDims.width}×${outputDims.height}` : 'unknown source size' }}
              </small>
            </label>
            <label class="fld">
              <span>Seed</span>
              <span class="seed-row">
                <input v-model.number="seed" type="number" />
                <button class="icon-btn" title="Randomize" @click="seed = randomSeed()">🎲</button>
              </span>
            </label>
          </div>
          <LoraListEditor
            label="LoRAs"
            :entries="loras"
            @change="loras = $event"
          />
        </div>
      </div>

      <label class="fld i2v-prompt">
        <span>MiniMax I2VA prompt (editable — Generate uses this text)</span>
        <textarea v-model="prompt" rows="10" spellcheck="false" />
      </label>
      <div class="lint-bar">
        <button
          class="btn"
          :disabled="!canApplyFixes"
          :title="fixes.length
            ? 'Rewrite the fixable findings below into MiniMax guide phrasing'
            : 'Nothing the linter can fix automatically'"
          @click="onApplyFixes"
        >
          Apply fixes{{ fixes.length ? ` (${fixes.length})` : '' }}
        </button>
        <span v-if="fixes.length" class="lint-bar-hint">
          The prompt is only changed when you press this.
        </span>
      </div>
      <ul v-if="fixes.length" class="lint lint-fixable">
        <li v-for="(f, i) in fixes" :key="'fix-' + i">
          <span>⚠ {{ f.message }}</span>
          <span class="fix-row"><b>now</b> <code>{{ f.original }}</code></span>
          <span class="fix-row"><b>fix</b> <code>{{ f.replacement }}</code></span>
        </li>
      </ul>
      <ul v-if="plainWarnings.length" class="lint">
        <li v-for="(w, i) in plainWarnings" :key="i">⚠ {{ w }}</li>
      </ul>

      <footer class="i2v-footer">
        <button
          class="btn danger"
          :disabled="cancelling || !store.activeJobIds.length"
          :title="store.activeJobIds.length
            ? 'Stop the running video workflow and drop queued ones for this image'
            : 'No video is generating'"
          @click="onCancelAll"
        >
          {{ cancelling ? 'Cancelling…' : 'Cancel' }}
        </button>
        <button
          class="btn primary"
          :disabled="submitting || !prompt.trim()"
          @click="onGenerate"
        >
          {{ submitting ? 'Submitting…' : 'Generate' }}
        </button>
      </footer>

      <div class="i2v-strip">
        <div
          v-for="[jobId, chip] in store.jobs"
          :key="'job-' + jobId"
          class="tile tile-job"
          :class="{ failed: chip.state === 'failed' }"
          :title="chip.error ?? undefined"
        >
          <span>{{ jobLabel(chip) }}</span>
          <button
            v-if="chip.state === 'failed'"
            class="icon-btn"
            title="Dismiss"
            @click="store.dismissJob(jobId)"
          >✕</button>
          <button
            v-else
            class="icon-btn"
            title="Cancel this job"
            :disabled="chip.cancelling"
            @click="onCancelJob(jobId)"
          >✕</button>
        </div>
        <div v-for="(v, idx) in store.videos" :key="v.id" class="tile-wrap">
          <div
            class="tile"
            :title="`seed ${v.seed ?? '—'} · ${v.duration_s ?? '—'}s · ${v.quality ?? ''}`
              + `${v.width && v.height ? ` · ${v.width}×${v.height}` : ''}`"
            @dblclick="viewerIndex = idx"
          >
            <img :src="thumbnailUrl(v.file_path)" alt="" />
            <button class="overlay play" title="Play" @click.stop="viewerIndex = idx">▶</button>
            <button
              class="overlay star"
              :class="{ active: v.is_favorite }"
              title="Favorite"
              @click.stop="onToggleFavorite(v)"
            >★</button>
            <button class="overlay del" title="Delete" @click.stop="onDeleteVideo(v)">✕</button>
          </div>
          <div class="tile-meta">
            <span class="tile-meta-quality" :title="videoDetails[idx].quality">
              {{ videoDetails[idx].quality }}
            </span>
            <span :title="videoDetails[idx].timing">{{ videoDetails[idx].timing }}</span>
            <span>{{ videoDetails[idx].generated }}</span>
          </div>
        </div>
        <div v-if="!store.videos.length && !store.jobs.size" class="strip-empty">
          No videos yet — generated clips appear here.
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
  </div>
</template>

<style scoped>
/* .dialog-overlay is scoped per dialog (see ConfigDialog.vue) — rules
   copied from there to match the sibling dialogs' overlay/card idiom.
   Strip CSS adapts ShotHeader.vue's .sh-takes-row / .sh-tile (fixed
   176x99 tiles, overflow-x auto, hover overlays). */
.dialog-overlay {
  position: fixed;
  inset: 0;
  z-index: 900;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
}

.i2v-card {
  background: var(--surface-section);
  border-radius: 12px;
  padding: 22px 28px 24px;
  width: min(980px, 94vw);
  max-height: 92vh;
  overflow-y: auto;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
}

.i2v-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
}

.i2v-header h3 {
  margin: 0;
  font-size: 18px;
  color: var(--text-color);
}

.icon-btn {
  border: none;
  background: none;
  color: var(--text-color-secondary);
  cursor: pointer;
  font-size: 14px;
  padding: 4px;
}

.icon-btn:hover {
  color: var(--text-color);
}

.i2v-top { display: flex; gap: 16px; }
.i2v-source { width: 280px; height: auto; object-fit: contain; border-radius: 6px; }
.i2v-controls { flex: 1; display: flex; flex-direction: column; gap: 10px; }

.fld {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: var(--text-color-secondary);
}

.fld textarea,
.fld select,
.fld input {
  font-family: inherit;
  font-size: 13px;
  color: var(--text-color);
  background: var(--surface-ground);
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  padding: 6px 8px;
  box-sizing: border-box;
}

.btn {
  align-self: flex-start;
  padding: 6px 14px;
  border-radius: 6px;
  border: 1px solid var(--surface-border);
  background: var(--surface-card, var(--surface-ground));
  color: var(--text-color);
  cursor: pointer;
  font-size: 13px;
}

.btn:disabled { opacity: 0.6; cursor: default; }

.btn.danger {
  border-color: #c33;
  color: #c33;
}

.btn.danger:disabled {
  border-color: var(--surface-border);
  color: var(--text-color-secondary);
}

.btn.primary {
  background: var(--primary-color);
  border-color: var(--primary-color);
  color: var(--primary-color-text, #fff);
}

.params { display: flex; gap: 12px; flex-wrap: wrap; }
.seed-row { display: inline-flex; gap: 4px; }
.i2v-prompt { margin-top: 14px; }
.dims-hint { color: var(--text-muted, #888); font-size: 11px; margin-top: 2px; }
.i2v-prompt textarea { width: 100%; font-family: monospace; font-size: 12px; }
.lint { color: var(--warn, #c90); font-size: 12px; margin: 4px 0; }
.lint-bar { display: flex; align-items: center; gap: 10px; margin-top: 8px; }
.lint-bar-hint { font-size: 11px; color: var(--text-color-secondary); }
.lint-fixable { list-style: none; padding-left: 0; }
.lint-fixable li { display: flex; flex-direction: column; gap: 2px; margin-bottom: 8px; }
.fix-row { display: flex; gap: 6px; align-items: baseline; color: var(--text-color-secondary); }
.fix-row b { flex: 0 0 26px; font-weight: 600; font-size: 10px; text-transform: uppercase; }
.fix-row code {
  font-family: monospace;
  font-size: 11px;
  color: var(--text-color);
  white-space: pre-wrap;
  word-break: break-word;
}
.i2v-footer { display: flex; justify-content: flex-end; gap: 8px; margin-top: 10px; }
.fld-off { opacity: 0.55; }
.i2v-strip { display: flex; gap: 8px; overflow-x: auto; padding: 8px 0; min-height: 110px; align-items: flex-start; }
.tile-wrap { flex: 0 0 176px; display: flex; flex-direction: column; gap: 4px; }
/* Inside the column wrapper the main axis is vertical, so the tile's
   176px flex-basis would become its HEIGHT; pin both axes instead. */
.tile-wrap .tile { flex: 0 0 99px; width: 176px; }
.tile-meta { display: flex; flex-direction: column; font-size: 11px; line-height: 1.35; color: var(--text-color-secondary); }
.tile-meta span { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.tile-meta-quality { color: var(--text-color); font-weight: 600; }
.tile { position: relative; flex: 0 0 176px; height: 99px; border-radius: 6px; overflow: hidden; background: #111; }
.tile img { width: 100%; height: 100%; object-fit: cover; }
.tile .overlay { position: absolute; opacity: 0; transition: opacity .15s; }
.tile:hover .overlay { opacity: 1; }
.tile .play { inset: 0; margin: auto; width: 36px; height: 36px; }
.tile .star { top: 4px; left: 4px; }
.tile .star.active { opacity: 1; color: gold; }
.tile .del { top: 4px; right: 4px; }
.tile-job { display: flex; align-items: center; justify-content: center; gap: 6px; border: 1px dashed #666; }
.tile-job.failed { border-color: #c33; color: #c33; }
.strip-empty { color: #888; align-self: center; font-size: 13px; }
</style>
