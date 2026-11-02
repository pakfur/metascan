<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ApiError, streamUrl, thumbnailUrl } from '../../api/client'
import CompareViewer from '../generation/CompareViewer.vue'
import JobTile from '../generation/JobTile.vue'
import SeedControls from '../generation/SeedControls.vue'
import { useBackgroundStore } from '../../stores/background'
import { useUpscaleStore } from '../../stores/upscale'
import { useToast } from '../../composables/useToast'
import { useWebSocket } from '../../composables/useWebSocket'
import type { Media } from '../../types/media'
import { randomSeed, type SeedPolicy } from '../../types/t2i'
import {
  candidateLabel,
  defaultWorkflowId,
  workflowsFor,
  type UpscaleCandidate,
  type UpscaleWorkflow,
} from '../../types/upscale'

const props = defineProps<{ media: Media }>()
const emit = defineEmits<{ close: [] }>()

const store = useUpscaleStore()
const background = useBackgroundStore()
const toast = useToast()

const ready = ref(false)
const failed = ref<string | null>(null)
const selectedId = ref<number | null>(null)
const workflowId = ref<number | null>(null)
const count = ref(1)
const seed = ref(randomSeed())
const seedPolicy = ref<SeedPolicy>('random')
const prompt = ref('')
const scale = ref<number | null>(null)
const starting = ref(false)
const swapping = ref(false)
const viewer = ref<InstanceType<typeof CompareViewer> | null>(null)
// A viewer that let go of its files for a swap comes back with this key.
const viewerKey = ref(0)

useWebSocket('upscale', (event, data) => store.handleUpscaleEvent(event, data))
useWebSocket('comfy', (event, data) => store.handleComfyEvent(event, data))

const workflows = computed<UpscaleWorkflow[]>(() =>
  store.config ? workflowsFor(store.config, props.media) : [],
)
const workflow = computed(() => workflows.value.find((w) => w.id === workflowId.value) ?? null)
const selected = computed<UpscaleCandidate | null>(
  () => store.candidates.find((c) => c.id === selectedId.value) ?? null,
)
const sourcePath = computed(() => store.sourcePath ?? props.media.file_path)
const originalUrl = computed(() =>
  streamUrl(store.record ? store.record.original_path : sourcePath.value),
)
// A result being judged, else what the library holds now, else nothing yet.
const upscaledUrl = computed(() => {
  if (selected.value) return streamUrl(selected.value.file_path)
  return store.record ? streamUrl(sourcePath.value) : null
})
const seedless = computed(() => workflow.value !== null && !workflow.value.has_seed)
const single = computed(() => seedless.value || seedPolicy.value === 'fixed')
const maxCount = computed(() => store.config?.max_count ?? 8)
const busy = computed(() => store.jobs.size > 0 || store.sourceRuns.length > 0)
const paused = computed(() => {
  const state = background.processes.find((p) => p.id === 'upscale')?.state
  return state === 'paused' || state === 'held'
})
const runLabel = computed(() => (store.record ? 'Upscale again' : 'Upscale'))
const fromOriginal = computed(() =>
  store.record ? 'Runs on the original, not on the current upscaled file.' : '',
)

watch(
  () => store.candidates,
  (rows) => {
    // A new result is what the owner came to look at.
    if (selectedId.value !== null && !rows.some((c) => c.id === selectedId.value)) {
      selectedId.value = null
    }
    if (selectedId.value === null && rows.length) selectedId.value = rows[0].id
  },
)

function errorText(e: unknown): string {
  return e instanceof ApiError ? e.message : e instanceof Error ? e.message : String(e)
}

onMounted(async () => {
  try {
    await store.open(props.media)
    if (store.config) {
      workflowId.value = defaultWorkflowId(store.config, props.media)
      scale.value = store.config.default_scale
    }
    if (store.candidates.length) selectedId.value = store.candidates[0].id
    ready.value = true
  } catch (e) {
    failed.value = errorText(e)
  }
})

onBeforeUnmount(() => store.close())

async function onRun() {
  if (!ready.value || workflowId.value === null || starting.value) return
  starting.value = true
  try {
    const next = await store.run({
      source_path: sourcePath.value,
      preset_id: workflowId.value,
      count: single.value ? 1 : count.value,
      seed: workflow.value?.has_seed ? seed.value : null,
      seed_policy: seedPolicy.value,
      prompt: workflow.value?.has_prompt && prompt.value.trim() ? prompt.value : null,
      scale: workflow.value?.has_scale ? scale.value : null,
    })
    if (next !== null) seed.value = next
  } catch (e) {
    toast.show(errorText(e), 'warn', 5000)
  } finally {
    starting.value = false
  }
}

// The grid reloads from LibraryView's media_swapped handler; here the viewer
// only has to let go of the files first.
async function swap(action: () => Promise<unknown>) {
  swapping.value = true
  viewer.value?.release()
  try {
    await action()
  } catch (e) {
    toast.show(errorText(e), 'warn', 5000)
  } finally {
    swapping.value = false
    viewerKey.value += 1
    await nextTick()
  }
}

async function onAccept() {
  const c = selected.value
  if (!c) return
  await swap(() => store.accept(c.id))
}

async function onRevert() {
  if (!confirm('Put the original back and delete the upscaled file?')) return
  selectedId.value = null
  await swap(() => store.revert())
}

async function onDiscard(c: UpscaleCandidate) {
  try {
    await store.discard(c.id)
  } catch (e) {
    toast.show(errorText(e), 'warn', 5000)
  }
}

async function onCancelAll() {
  try {
    await store.cancelAll()
  } catch (e) {
    toast.show(errorText(e), 'warn', 5000)
  }
}

function onCountInput(e: Event) {
  const n = Math.floor(Number((e.target as HTMLInputElement).value))
  count.value = Number.isFinite(n) ? Math.min(maxCount.value, Math.max(1, n)) : 1
}
</script>

<template>
  <!-- No @click.self close, as for I2V: a stray click on the backdrop must not
       hide running jobs or the result being judged. -->
  <div class="dialog-overlay">
    <div class="up-card">
      <header class="up-header">
        <h3>Upscale</h3>
        <button class="icon-btn" title="Close" @click="emit('close')">✕</button>
      </header>

      <p v-if="failed" class="hint warn">{{ failed }}</p>

      <div v-else class="up-body">
        <section class="up-side" aria-label="Results">
          <h4 class="sec-title">Results</h4>
          <div class="tiles">
            <JobTile
              v-for="[jobId, chip] in store.jobs"
              :key="'job-' + jobId"
              :chip="chip"
              @cancel="store.cancelJob(jobId)"
              @dismiss="store.dismissJob(jobId)"
            />
            <div
              v-for="c in store.candidates"
              :key="c.id"
              class="tile"
              :class="{ selected: c.id === selectedId }"
              role="button"
              tabindex="0"
              :title="c.preset_name ?? undefined"
              @click="selectedId = c.id"
              @keydown.enter="selectedId = c.id"
            >
              <img :src="thumbnailUrl(c.file_path)" alt="" loading="lazy" />
              <button
                class="tile-x"
                title="Discard this result"
                @click.stop="onDiscard(c)"
              >✕</button>
              <span class="tile-label">{{ candidateLabel(c) }}</span>
              <span v-if="c.audio_lost" class="tile-warn">no audio</span>
            </div>
            <p v-if="!store.candidates.length && !store.jobs.size" class="hint">
              No results yet: they appear here.
            </p>
          </div>
        </section>

        <main class="up-main">
          <CompareViewer
            :key="viewerKey"
            ref="viewer"
            :original-url="originalUrl"
            :upscaled-url="upscaledUrl"
            :is-video="media.is_video"
          />
        </main>

        <aside class="up-form">
          <label class="fld">
            <span>Workflow</span>
            <select v-model.number="workflowId" :disabled="!workflows.length">
              <option v-for="w in workflows" :key="w.id" :value="w.id">{{ w.name }}</option>
            </select>
          </label>
          <p v-if="ready && !workflows.length" class="hint warn">
            No {{ media.is_video ? 'video' : 'image' }} upscale workflow is registered.
            Add one in Configuration, Upscale.
          </p>

          <label v-if="workflow?.has_prompt" class="fld">
            <span>Prompt</span>
            <textarea v-model="prompt" rows="3" placeholder="Optional" />
          </label>

          <label v-if="workflow?.has_scale && store.config" class="fld">
            <span>Scale</span>
            <select v-model.number="scale">
              <option v-for="s in store.config.scales" :key="s" :value="s">×{{ s }}</option>
            </select>
          </label>

          <SeedControls
            v-if="workflow?.has_seed"
            v-model:seed="seed"
            v-model:policy="seedPolicy"
          />

          <label class="fld">
            <span>Count</span>
            <input
              type="number"
              min="1"
              :max="maxCount"
              :value="single ? 1 : count"
              :disabled="single"
              @input="onCountInput"
            />
          </label>
          <p v-if="seedless" class="hint">
            This workflow has no seed, so every run gives the same result.
          </p>

          <div class="actions">
            <button
              class="btn primary"
              :disabled="!ready || workflowId === null || starting || swapping"
              @click="onRun"
            >{{ runLabel }}</button>
            <button v-if="busy" class="btn" @click="onCancelAll">Cancel</button>
          </div>
          <p v-if="fromOriginal" class="hint">{{ fromOriginal }}</p>
          <p v-if="paused" class="hint warn">
            Upscaling is paused: new work waits until it resumes.
          </p>
          <p v-if="store.note" class="hint warn">{{ store.note }}</p>

          <div class="decide">
            <template v-if="selected">
              <p class="hint">
                {{ selected.preset_name }}
                <template v-if="selected.seed !== null"> · seed {{ selected.seed }}</template>
              </p>
              <p v-if="selected.audio_lost" class="hint warn">
                This result has no soundtrack, and the original does. Its workflow
                does not carry audio through.
              </p>
              <div class="actions">
                <button class="btn primary" :disabled="swapping" @click="onAccept">Accept</button>
                <button class="btn" :disabled="swapping" @click="onDiscard(selected)">
                  Discard
                </button>
              </div>
            </template>
            <div v-if="store.record" class="actions">
              <button class="btn danger" :disabled="swapping" @click="onRevert">
                Revert to original
              </button>
            </div>
          </div>
        </aside>
      </div>
    </div>
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
.up-card {
  background: var(--surface-section);
  border-radius: 12px;
  padding: 16px 20px 20px;
  width: min(1400px, 94vw);
  height: 88vh;
  display: flex;
  flex-direction: column;
  gap: 12px;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
}
.up-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.up-header h3 { margin: 0; font-size: 18px; color: var(--text-color); }
.icon-btn {
  border: none;
  background: none;
  color: var(--text-color-secondary);
  cursor: pointer;
  font-size: 14px;
  padding: 4px;
}
.icon-btn:hover { color: var(--text-color); }
.up-body {
  flex: 1 1 auto;
  min-height: 0;
  display: grid;
  grid-template-columns: 180px minmax(0, 1fr) 300px;
  grid-template-rows: minmax(0, 1fr);
  gap: 16px;
}
.up-side,
.up-form {
  min-height: 0;
  overflow-y: auto;
  scrollbar-gutter: stable;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.up-main { min-width: 0; min-height: 0; }
.sec-title { margin: 0; font-size: 13px; color: var(--text-color); }
.tiles {
  display: flex;
  flex-direction: column;
  gap: 8px;
  --tile-size: 156px;
}
.tile {
  position: relative;
  width: var(--tile-size);
  height: var(--tile-size);
  border: 2px solid transparent;
  border-radius: 8px;
  overflow: hidden;
  cursor: pointer;
  background: var(--surface-ground);
}
.tile.selected { border-color: var(--primary-color); }
.tile img { width: 100%; height: 100%; object-fit: contain; }
.tile-x {
  position: absolute;
  top: 4px;
  right: 4px;
  border: none;
  border-radius: 4px;
  background: rgba(0, 0, 0, 0.6);
  color: #fff;
  cursor: pointer;
  font-size: 11px;
  padding: 2px 6px;
}
.tile-label,
.tile-warn {
  position: absolute;
  left: 4px;
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 11px;
  color: #fff;
  background: rgba(0, 0, 0, 0.6);
}
.tile-label { bottom: 4px; }
.tile-warn { bottom: 26px; background: #a33; }
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
.hint { margin: 0; font-size: 12px; color: var(--text-color-secondary); }
.hint.warn { color: #c33; }
.actions { display: flex; flex-wrap: wrap; gap: 8px; }
.decide {
  margin-top: auto;
  padding-top: 12px;
  border-top: 1px solid var(--surface-border);
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.btn {
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
.btn.danger { border-color: #c33; color: #c33; }
@media (max-width: 1099px) {
  .up-card { height: auto; max-height: 92vh; overflow-y: auto; }
  .up-body { display: flex; flex-direction: column; }
  .up-main { height: 420px; flex: none; }
  .tiles { flex-direction: row; flex-wrap: wrap; }
}
</style>
