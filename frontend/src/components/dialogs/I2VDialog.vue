<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import type { Media } from '../../types/media'
import type { I2vVideo } from '../../types/i2v'
import { useI2vStore } from '../../stores/i2v'
import { generatePrompt, generateVideo, deleteI2vVideo } from '../../api/i2v'
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

async function onGenerate() {
  if (!prompt.value.trim()) {
    toast.show('Write or generate a prompt first', 'warn')
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
      width: props.media.width ?? 0,
      height: props.media.height ?? 0,
      loras: loras.value,
      idea: idea.value,
    })
    warnings.value = res.warnings
    store.trackJob(res.job_id)
    toast.show('Video job queued', 'success')
  } catch (e) {
    toast.show(e instanceof Error ? e.message : String(e), 'warn')
  } finally {
    submitting.value = false
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

function jobLabel(chip: { state: string; value?: number; max?: number }): string {
  if (chip.state === 'running' && chip.value != null && chip.max) {
    return `${Math.round((chip.value / chip.max) * 100)}%`
  }
  return chip.state
}
</script>

<template>
  <div class="dialog-overlay" @click.self="close()">
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
      <ul v-if="warnings.length" class="lint">
        <li v-for="(w, i) in warnings" :key="i">⚠ {{ w }}</li>
      </ul>

      <footer class="i2v-footer">
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
            @click="store.dismissJob(jobId)"
          >✕</button>
        </div>
        <div
          v-for="(v, idx) in store.videos"
          :key="v.id"
          class="tile"
          :title="`seed ${v.seed ?? '—'} · ${v.duration_s ?? '—'}s · ${v.quality ?? ''}`"
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

.btn.primary {
  background: var(--primary-color);
  border-color: var(--primary-color);
  color: var(--primary-color-text, #fff);
}

.params { display: flex; gap: 12px; flex-wrap: wrap; }
.seed-row { display: inline-flex; gap: 4px; }
.i2v-prompt { margin-top: 14px; }
.i2v-prompt textarea { width: 100%; font-family: monospace; font-size: 12px; }
.lint { color: var(--warn, #c90); font-size: 12px; margin: 4px 0; }
.i2v-footer { display: flex; justify-content: flex-end; margin-top: 10px; }
.i2v-strip { display: flex; gap: 8px; overflow-x: auto; padding: 8px 0; min-height: 110px; }
.tile { position: relative; flex: 0 0 176px; height: 99px; border-radius: 6px; overflow: hidden; background: #111; }
.tile img { width: 100%; height: 100%; object-fit: cover; }
.tile .overlay { position: absolute; opacity: 0; transition: opacity .15s; }
.tile:hover .overlay { opacity: 1; }
.tile .play { inset: 0; margin: auto; width: 36px; height: 36px; }
.tile .star { top: 4px; left: 4px; }
.tile .star.active { opacity: 1; color: gold; }
.tile .del { top: 4px; right: 4px; }
.tile-job { display: flex; align-items: center; justify-content: center; border: 1px dashed #666; }
.tile-job.failed { border-color: #c33; color: #c33; }
.strip-empty { color: #888; align-self: center; font-size: 13px; }
</style>
