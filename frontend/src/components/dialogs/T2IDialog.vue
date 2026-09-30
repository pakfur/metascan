<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, shallowRef, watch } from 'vue'
import { useDebounceFn, useEventListener } from '@vueuse/core'
import {
  DEFAULT_MAX_BATCH_SIZE,
  DEFAULT_MAX_COUNT_PER_BATCH,
  SEED_MAX,
  activeFilterCount,
  effectiveBatchSize,
  effectiveCount,
  formatT2iTimestamp,
  isCountEffective,
  plannedImages,
  randomSeed,
  t2iImageToMedia,
  toStripItem,
  withCurrentOption,
  type T2iFormState,
} from '../../types/t2i'
import type { ThumbItem } from '../../types/jobs'
import type { Media } from '../../types/media'
import type { WorkflowPreset } from '../../types/storyboard'
import { ApiError } from '../../api/client'
import { listPresets } from '../../api/comfy'
import { useFoldersStore } from '../../stores/folders'
import { useT2iStore } from '../../stores/t2i'
import { useFormAutosave } from '../../composables/useFormAutosave'
import { useToast } from '../../composables/useToast'
import { useWebSocket } from '../../composables/useWebSocket'
import {
  buildBatchRequest,
  defaultPresetFor,
  generateBlocker,
  initialFields,
  mergeFormState,
  requestSignature,
  savableForm,
  toFormState,
  unlockPatch,
  usableDraft,
  type T2iFields,
} from '../../utils/t2iDialogForm'
import CaptionFilterPopover from '../t2i/CaptionFilterPopover.vue'
import JobTile from '../generation/JobTile.vue'
import SeedControls from '../generation/SeedControls.vue'
import ThumbStrip from '../generation/ThumbStrip.vue'
import LoraListEditor from '../storyboard/LoraListEditor.vue'
import MediaViewer from '../viewer/MediaViewer.vue'

const emit = defineEmits<{ close: [] }>()

const store = useT2iStore()
const foldersStore = useFoldersStore()
const toast = useToast()

const errMsg = (e: unknown): string => (e instanceof Error ? e.message : String(e))

// ---- the form ---------------------------------------------------------------------
// One reactive object holds every input (utils/t2iDialogForm.ts owns the rules).
// It stays inert (`ready` false) until the settings and workflows have loaded,
// and only then takes the configured defaults and the saved draft.
const form = reactive<T2iFields>(initialFields(null, [], 0))
const ready = ref(false)
const presets = ref<WorkflowPreset[]>([])
// Signature of the last Manual submit this dialog made (or the image it last
// loaded): component-local on purpose, so an earlier session never counts.
let lastSubmitted: string | null = null

// While an image is selected every change to the form is saved INTO THAT IMAGE's
// form_state (never its as-rendered facts), 600 ms after the last edit, one save
// at a time and in order. With none selected the form is a scratch area and
// nothing is saved (the draft below is the only thing kept).
const autosave = useFormAutosave<T2iFormState>({
  form: () => toFormState(form),
  selectedId: () => store.selectedId,
  save: (id, fields) => store.saveFormState(id, fields),
  savable: savableForm,
})
const saveFailed = autosave.saveFailed

const models = computed(() => store.config?.models ?? [])
const hasNegative = computed(() => models.value.find((m) => m.id === form.model)?.has_negative ?? false)
const csvAvailable = computed(() => store.config?.csv.available ?? false)
const maxBatchSize = computed(() => store.config?.max_batch_size ?? DEFAULT_MAX_BATCH_SIZE)
const maxCount = computed(() => store.config?.max_count_per_batch ?? DEFAULT_MAX_COUNT_PER_BATCH)
const seedMax = computed(() => store.config?.seed_max ?? SEED_MAX)

// A stored value that has since left a list must still be shown, not blanked.
const modelOptions = computed(() =>
  form.model && !models.value.some((m) => m.id === form.model)
    ? [...models.value, { id: form.model, label: `${form.model} (not configured)` }]
    : models.value,
)
const sizeOptions = computed(() => withCurrentOption(store.config?.megapixels ?? [1], form.megapixels))
const aspectOptions = computed(() => {
  const list = store.config?.aspect_ratios ?? []
  return shownAspect.value && !list.includes(shownAspect.value) ? [...list, shownAspect.value] : list
})
const presetMissing = computed(
  () => form.presetId !== null && !presets.value.some((p) => p.id === form.presetId),
)

async function loadPresets() {
  try {
    presets.value = (await listPresets()).filter((p) => p.kind === 't2i')
  } catch (e) {
    presets.value = []
    toast.show(`Couldn't load the workflows: ${errMsg(e)}`, 'warn', 5000)
  }
}

function initForm() {
  const cfg = store.config
  if (!cfg) return // the load failed: the Retry bar is showing
  const ids = presets.value.map((p) => p.id)
  const fresh = initialFields(cfg, ids, randomSeed(cfg.seed_max))
  const draft = store.loadDraft()
  Object.assign(form, draft ? mergeFormState(fresh, usableDraft(draft, cfg, ids)) : fresh)
  lastSubmitted = null
  ready.value = true
}

let disposed = false
onMounted(async () => {
  const workflows = loadPresets() // independent of the store's session
  await store.open() // never rejects: every loader reports its own failure
  await workflows
  if (!disposed) initForm()
})

async function retryLoad() {
  const workflows = loadPresets()
  await store.open()
  await workflows
  if (!disposed) initForm()
}

// Choosing a model selects its default workflow, when the config names one.
// A change handler, not a watcher: a watcher would also fire when a saved
// form is loaded, and silently swap the workflow it was rendered with.
function onModelChange(id: string) {
  form.model = id
  const preset = defaultPresetFor(store.config, id, presets.value.map((p) => p.id))
  if (preset !== null) form.presetId = preset
}

// ---- live connection --------------------------------------------------------------------
// useWebSocket needs the component setup context, so the dialog subscribes and
// forwards every frame to the store, which never subscribes itself.
const { connected } = useWebSocket('t2i', (event, data) => store.handleT2iEvent(event, data))
useWebSocket('comfy', (event, data) => store.handleComfyEvent(event, data))
// After a reconnect some frames were missed: rebuild from the server.
watch(connected, (up) => {
  if (up && ready.value) void store.reattach()
})

// ---- Random batches take over the boxes ----------------------------------------------------
// While a Random batch runs, Caption / Prompt / Negative / Aspect / Seed are
// read-only live views of its current step. They are a display only: the form
// itself is untouched until the batch ends, when the last step's values are
// copied in (a finished batch hands its values over exactly once).
const randomLocked = computed(() => store.randomBatch !== null)
const step = computed(() => (randomLocked.value ? store.currentStep : null))
const shownCaption = computed(() => (randomLocked.value ? (step.value?.caption ?? '') : form.caption))
const shownPrompt = computed(() => (randomLocked.value ? (step.value?.prompt ?? '') : form.prompt))
const shownNegative = computed(() => (randomLocked.value ? (step.value?.negative ?? '') : form.negative))
const shownAspect = computed(() => (step.value ? step.value.aspect_ratio : form.aspect))
const shownSeed = computed(() => (step.value ? step.value.seed : form.seed))
const stepWarnings = computed(() => step.value?.warnings ?? [])

watch(
  () => store.lastFinished,
  (finished) => {
    if (!finished || !ready.value) return
    // An image being edited owns the form: its values are never overwritten.
    const patch = store.selectedId === null ? unlockPatch(finished, hasNegative.value) : null
    if (patch) Object.assign(form, mergeFormState(form, patch))
    if (finished.outcome === 'complete') {
      const failed = finished.images_failed > 0 ? `, ${finished.images_failed} failed` : ''
      const n = finished.images_done
      toast.show(`Batch complete: ${n} image${n === 1 ? '' : 's'}${failed}`, finished.images_failed > 0 ? 'warn' : 'success')
    }
  },
)

// ---- caption, resolved caption, prompt -----------------------------------------------------------
const generating = ref(false)
const rolling = ref(false)
const promptWarnings = ref<string[]>([])
const negativeOpen = ref(false)

// The Resolved caption: the caption with its __TOKEN__s filled in for the
// current seed and model. `key` says what it was resolved for, so it can be
// shown dimmed while it is out of date and refreshed when it is open.
const resolvedOpen = ref(false)
const resolved = ref<{ key: string; text: string; warnings: string[] } | null>(null)
const resolvedKey = () => JSON.stringify([form.caption, form.seed, form.model])
const resolvedStale = computed(() => resolved.value !== null && resolved.value.key !== resolvedKey())

async function refreshResolved() {
  if (!form.caption.trim() || !form.model) {
    resolved.value = null
    return
  }
  const key = resolvedKey()
  if (resolved.value?.key === key) return
  const r = await store.resolveCaption({ caption: form.caption, seed: form.seed, model: form.model })
  if (r && resolvedKey() === key) resolved.value = { key, text: r.resolved_caption, warnings: r.warnings }
}
const refreshResolvedSoon = useDebounceFn(refreshResolved, 400)

watch(
  () => [form.caption, form.seed, form.model],
  () => {
    if (resolvedOpen.value && !randomLocked.value) void refreshResolvedSoon()
  },
)

function toggleResolved() {
  resolvedOpen.value = !resolvedOpen.value
  if (resolvedOpen.value) void refreshResolved()
}

// Review-only: writes the prompt into the box and shows what the caption
// resolved to, and never starts a render. A 502 means the language model
// failed (or timed out): the caption is fine and a retry usually works.
async function onGeneratePrompt() {
  if (!form.caption.trim() || !form.model || generating.value) return
  const body = { caption: form.caption, seed: form.seed, model: form.model }
  const target = store.selectedId
  generating.value = true
  try {
    const r = await store.generatePrompt(body)
    if (store.selectedId !== target) {
      // The user loaded another image while the prompt was being written:
      // filling its box would edit (and autosave into) an image they did not ask about.
      toast.show('The prompt was ready, but you switched images meanwhile, so it was not used.', 'info', 4000)
      return
    }
    form.prompt = r.prompt
    if (hasNegative.value && r.negative !== null) form.negative = r.negative
    promptWarnings.value = r.warnings
    resolved.value = { key: JSON.stringify([body.caption, body.seed, body.model]), text: r.resolved_caption, warnings: r.warnings }
    resolvedOpen.value = true
  } catch (e) {
    toast.show(
      e instanceof ApiError && e.status === 502
        ? `The prompt writer failed (${e.message}). Press Generate Prompt to try again.`
        : `Couldn't write the prompt: ${errMsg(e)}`,
      'warn',
      6000,
    )
  } finally {
    generating.value = false
  }
}

// The dice loads one caption (and its aspect ratio) from the caption file so
// it can be read and edited before anything is rendered.
async function onRoll() {
  if (rolling.value) return
  const target = store.selectedId
  rolling.value = true
  try {
    const row = await store.rollCaption(form.filter)
    if (store.selectedId !== target) return // another image was loaded meanwhile
    form.caption = row.caption
    if (store.config?.aspect_ratios.includes(row.aspect_ratio)) form.aspect = row.aspect_ratio
  } catch (e) {
    toast.show(
      e instanceof ApiError && e.status === 404
        ? 'No caption matches the current filter.'
        : `Couldn't load a caption: ${errMsg(e)}`,
      'warn',
    )
  } finally {
    rolling.value = false
  }
}

// ---- the caption filter ----------------------------------------------------------------------------
const filterOpen = ref(false)
const filterAnchor = ref<HTMLElement | null>(null)
const filterStyle = ref<Record<string, string>>({})
const filterCount = ref<number | null>(null)
const filterTotal = computed(() => store.captionMeta?.total ?? store.config?.csv.total ?? 0)
const filterActive = computed(() => activeFilterCount(form.filter))

// The store answers null for a failed or superseded request: the label then
// stays as it was ("..." if nothing had arrived).
const refreshFilterCount = useDebounceFn(async () => {
  const r = await store.countCaptions(form.filter)
  if (r) filterCount.value = r.count
}, 300)

// The panel is `position: fixed` under the Filter button, right edges aligned,
// and no taller than the room below it: inside the dialog card (which scrolls
// and clips) its footer, where the match count is, would be cut off.
function placeFilter() {
  const el = filterAnchor.value
  if (!el) return
  const box = el.getBoundingClientRect()
  const top = box.bottom + 6
  filterStyle.value = {
    top: `${top}px`,
    right: `${Math.max(8, window.innerWidth - box.right)}px`,
    maxHeight: `${Math.max(240, window.innerHeight - top - 16)}px`,
  }
}
useEventListener('resize', () => {
  if (filterOpen.value) placeFilter()
})

function askFilterCount() {
  filterCount.value = null
  void refreshFilterCount()
}
watch(() => form.filter, () => {
  if (filterOpen.value) askFilterCount()
})
watch(filterOpen, (open) => {
  if (!open) return
  // The filter options failed to load when the dialog opened (the store said
  // so): opening the panel is the moment to try again.
  if (!store.captionMeta && !store.metaLoading) void store.loadCaptionMeta()
  void nextTick(placeFilter)
  askFilterCount()
})

// ---- batch numbers ------------------------------------------------------------------------------------
const shownBatchSize = computed(() => effectiveBatchSize(form.mode, form.batchSize, maxBatchSize.value))
const countIgnored = computed(() => !isCountEffective(form.seedPolicy, form.countPerBatch))
const planned = computed(() =>
  plannedImages(form.mode, form.seedPolicy, form.batchSize, form.countPerBatch, {
    maxBatchSize: maxBatchSize.value,
    maxCount: maxCount.value,
  }),
)
const generateLabel = computed(() => (planned.value > 1 ? `Generate ×${planned.value}` : 'Generate'))

// A committed number settles on what will really be used (whole, within the limits).
function onBatchSize(e: Event) {
  const input = e.target as HTMLInputElement
  form.batchSize = effectiveBatchSize('random', Number(input.value), maxBatchSize.value)
  input.value = String(form.batchSize)
}
function onCountPerBatch(e: Event) {
  const input = e.target as HTMLInputElement
  form.countPerBatch = effectiveCount('increment', Number(input.value), maxCount.value)
  input.value = String(form.countPerBatch)
}

// ---- Generate / Cancel -----------------------------------------------------------------------------------
const submitting = ref(false)
const cancelling = ref(false)
const batchWarnings = ref<string[]>([])

const blocker = computed(() =>
  generateBlocker(form, {
    ready: ready.value,
    csvAvailable: csvAvailable.value,
    randomRunning: randomLocked.value,
    submitting: submitting.value,
  }),
)

function startErrorText(e: unknown): string {
  if (e instanceof ApiError && e.status === 409) {
    return 'A Random batch is already running. Cancel it or wait for it to finish.'
  }
  return errMsg(e)
}

async function onGenerate() {
  if (blocker.value !== null) return
  // The server owns the run: one snapshot of the form goes out, and nothing
  // typed afterwards can reach a batch that is already running.
  const req = buildBatchRequest(form, {
    hasNegative: hasNegative.value,
    maxBatchSize: maxBatchSize.value,
    maxCount: maxCount.value,
  })
  const signature = req.mode === 'manual' ? requestSignature(form, hasNegative.value) : null
  if (
    signature !== null &&
    signature === lastSubmitted &&
    !confirm(
      'Nothing has changed since the last image this dialog rendered or loaded — same seed, ' +
        'model, workflow, size, aspect ratio and prompt. This will render an identical image.\n\n' +
        'Generate it again anyway? (Cancel, then 🎲 for a new seed.)',
    )
  ) {
    return
  }
  submitting.value = true
  try {
    const r = await store.startBatch(req)
    // A batch makes NEW images, so an image being edited is saved (pending edits
    // go in as usual) and let go before anything below touches the form: neither
    // the seed advance nor a Random batch's live and unlocked values can then
    // reach it, and its saved form stays what produced it. The form keeps its
    // values as a scratch area.
    autosave.flush()
    store.clearSelection()
    if (signature !== null) lastSubmitted = signature
    batchWarnings.value = r.warnings
    promptWarnings.value = []
    // The box then shows the next unused seed (unless the user already changed it).
    if (req.mode === 'manual' && r.next_seed !== null && form.seed === req.seed) form.seed = r.next_seed
    toast.show(
      r.total_images === 1 ? 'Image job queued' : `${r.total_images} image jobs queued`,
      'success',
    )
  } catch (e) {
    toast.show(startErrorText(e), 'warn', 6000)
  } finally {
    submitting.value = false
  }
}

// Cancels every running batch (the server also cancels their ComfyUI jobs).
async function onCancel() {
  if (cancelling.value) return
  cancelling.value = true
  try {
    await store.cancelAll()
    toast.show('Cancelled', 'success')
  } catch (e) {
    toast.show(errMsg(e), 'warn')
  } finally {
    cancelling.value = false
  }
}

// ---- the strip: select, favourite, delete, view ---------------------------------------------------------------
const stripItems = computed(() => store.images.map(toStripItem))

// Clicking an image loads its saved form_state into the form and starts
// autosaving into it. Seed policy, Batch Size and Count per Batch stay as they
// are, and nothing is started (a stored Random mode only restores the mode
// and its filter). The strip only reports the FIRST click of a double-click,
// so opening the viewer never selects twice.
function onSelectTile(item: ThumbItem) {
  const id = Number(item.id)
  if (!ready.value || id === store.selectedId) return
  if (randomLocked.value) {
    toast.show('A Random batch is running: wait for it to finish before loading an image.', 'info')
    return
  }
  autosave.flush() // pending edits belong to the image being left
  const stored = store.selectImage(id)
  if (!stored) return
  Object.assign(form, mergeFormState(form, stored))
  // The form now equals what the server holds, so loading must not echo back
  // as a save. An untouched Generate would render the same image again.
  autosave.markLoaded()
  lastSubmitted = requestSignature(form, hasNegative.value)
}

// The form keeps its values and goes back to being a scratch area.
function stopEditing() {
  autosave.flush()
  store.clearSelection()
}

async function onFavorite(item: ThumbItem) {
  try {
    await store.toggleFavorite(Number(item.id))
  } catch (e) {
    toast.show(errMsg(e), 'warn')
  }
}

async function onDelete(item: ThumbItem) {
  if (!confirm('Delete this image? The file goes to the OS trash.')) return
  const id = Number(item.id)
  // Drop a pending save rather than PATCH a row that is about to go.
  if (store.selectedId === id) autosave.cancelPending()
  try {
    await store.deleteImage(id)
    void foldersStore.refreshT2iPaths()
  } catch (e) {
    toast.show(errMsg(e), 'warn')
  }
}

async function onCancelJob(jobId: number) {
  try {
    await store.cancelJob(jobId)
  } catch (e) {
    toast.show(errMsg(e), 'warn')
  }
}

// The viewer gets a snapshot taken when it opens: images arriving mid-batch
// would otherwise shift the list under its index.
const viewerImages = shallowRef<Media[]>([])
const viewerIndex = ref<number | null>(null)
function onOpenViewer(index: number) {
  viewerImages.value = store.images.map(t2iImageToMedia)
  viewerIndex.value = index
}

// ---- the scratch-form draft ---------------------------------------------------------------------------------
// Per-viewer convenience: what is in the boxes survives closing the dialog
// (every storage access is guarded inside the store). It is written only
// while the form is a scratch area, and only once it has been initialised, so
// blank defaults can never overwrite a saved draft.
function saveDraftNow() {
  if (ready.value && store.selectedId === null) store.saveDraft(toFormState(form))
}
const saveDraftSoon = useDebounceFn(() => {
  if (!disposed) saveDraftNow()
}, 500)
watch(
  () => JSON.stringify(toFormState(form)),
  () => void saveDraftSoon(),
)
// Back to the scratch form ("Stop editing", or the image vanished): what is in
// the boxes becomes the draft.
watch(
  () => store.selectedId,
  (id) => {
    if (id === null) void saveDraftSoon()
  },
)

function close() {
  autosave.flush() // before store.close() lets go of the selected image
  saveDraftNow()
  store.close()
  emit('close')
}

onBeforeUnmount(() => {
  saveDraftNow()
  disposed = true
})
</script>

<template>
  <!-- No @click.self close, deliberately: a stray click on the backdrop must
       not throw away a written prompt or hide running batches. The header ✕
       is the only way out. -->
  <div class="dialog-overlay">
    <div
      class="t2i-card"
      role="dialog"
      aria-modal="true"
      aria-labelledby="t2i-title"
      @scroll="filterOpen && placeFilter()"
    >
      <header class="t2i-header">
        <h3 id="t2i-title">Text to Image</h3>
        <button type="button" class="icon-btn" title="Close" aria-label="Close" @click="close()">✕</button>
      </header>

      <div v-if="!store.config && store.configError" class="bar failed" role="alert">
        <span>Couldn't load the Text to Image settings: {{ store.configError }}</span>
        <button type="button" class="link-btn" @click="retryLoad">Retry</button>
      </div>
      <p v-else-if="!ready" class="note">Loading…</p>

      <fieldset class="t2i-form" :disabled="!ready">
        <div class="grid-row">
          <label class="lbl" for="t2i-caption">Caption</label>
          <div class="stack">
            <textarea
              id="t2i-caption"
              class="area"
              rows="3"
              spellcheck="false"
              :value="shownCaption"
              :readonly="randomLocked"
              :placeholder="randomLocked ? 'A caption is drawn from the caption file for each step…' : 'Describe the image. Character tokens like __ALICE__ and __HAIR__ are filled in for you.'"
              @input="form.caption = ($event.target as HTMLTextAreaElement).value"
            />
            <div>
              <button
                type="button"
                class="link-btn fold"
                :aria-expanded="resolvedOpen"
                :disabled="randomLocked"
                @click="toggleResolved"
              >{{ resolvedOpen ? '▾' : '▸' }} Resolved caption</button>
            </div>
            <div v-if="resolvedOpen && !randomLocked" class="resolved">
              <p v-if="!form.caption.trim()" class="note">Nothing to resolve yet: write a caption first.</p>
              <template v-else-if="resolved">
                <textarea
                  class="area resolved-text"
                  :class="{ stale: resolvedStale }"
                  rows="4"
                  readonly
                  spellcheck="false"
                  aria-label="Resolved caption"
                  :value="resolved.text"
                />
                <ul v-if="resolved.warnings.length" class="lint">
                  <li v-for="w in resolved.warnings" :key="w">⚠ {{ w }}</li>
                </ul>
              </template>
              <p v-else class="note">Resolving…</p>
            </div>
          </div>
        </div>

        <div class="grid-row">
          <span class="lbl" />
          <div class="caption-actions">
            <button
              type="button"
              class="btn"
              :disabled="generating || randomLocked || !form.caption.trim() || !form.model"
              @click="onGeneratePrompt"
            >{{ generating ? 'Writing prompt…' : 'Generate Prompt' }}</button>
            <label class="radio">
              <input v-model="form.mode" type="radio" name="t2i-mode" value="manual" :disabled="randomLocked" />
              Manual
            </label>
            <label class="radio" :title="csvAvailable ? '' : (store.config?.csv.error ?? 'The caption file is not available')">
              <input
                v-model="form.mode"
                type="radio"
                name="t2i-mode"
                value="random"
                :disabled="randomLocked || !csvAvailable"
              />
              Random Caption
            </label>
            <button
              type="button"
              class="icon-btn dice"
              title="Load a random caption from the caption file"
              aria-label="Roll a random caption"
              :disabled="rolling || randomLocked || !csvAvailable"
              @click="onRoll"
            >🎲</button>
            <span ref="filterAnchor" class="filter-anchor">
              <button
                type="button"
                class="btn"
                :aria-expanded="filterOpen"
                :disabled="!csvAvailable"
                @click="filterOpen = !filterOpen"
              >Filter<span v-if="filterActive > 0" class="badge">{{ filterActive }}</span></button>
              <CaptionFilterPopover
                v-if="filterOpen"
                v-model="form.filter"
                class="filter-pop"
                :style="filterStyle"
                :meta="store.captionMeta"
                :count="filterCount"
                :total="filterTotal"
                @close="filterOpen = false"
              />
            </span>
          </div>
        </div>

        <div class="grid-row">
          <span class="lbl" />
          <div class="params">
            <label class="fld">
              <span>Model</span>
              <select :value="form.model" @change="onModelChange(($event.target as HTMLSelectElement).value)">
                <option v-for="m in modelOptions" :key="m.id" :value="m.id">{{ m.label }}</option>
              </select>
            </label>
            <label class="fld">
              <span>Workflow</span>
              <select v-model="form.presetId">
                <option :value="null" disabled>Choose a workflow…</option>
                <option v-if="presetMissing" :value="form.presetId" disabled>Workflow #{{ form.presetId }} (not found)</option>
                <option v-for="p in presets" :key="p.id" :value="p.id">{{ p.name }}</option>
              </select>
              <small v-if="ready && !presets.length" class="hint">none registered: add one under Configuration</small>
            </label>
            <label class="fld">
              <span>Size</span>
              <select v-model="form.megapixels">
                <option v-for="m in sizeOptions" :key="m" :value="m">{{ m }} MP</option>
              </select>
            </label>
            <label class="fld">
              <span>Aspect Ratio</span>
              <select
                :value="shownAspect"
                :disabled="randomLocked"
                :title="form.mode === 'random' ? 'A Random batch takes the aspect ratio of each caption' : ''"
                @change="form.aspect = ($event.target as HTMLSelectElement).value"
              >
                <option v-for="a in aspectOptions" :key="a" :value="a">{{ a }}</option>
              </select>
              <small v-if="form.mode === 'random' && !randomLocked" class="hint">taken from each caption</small>
            </label>
            <SeedControls
              :seed="shownSeed"
              :policy="form.seedPolicy"
              :disabled="randomLocked"
              :max="seedMax"
              @update:seed="form.seed = $event"
              @update:policy="form.seedPolicy = $event"
            />
          </div>
        </div>

        <div class="grid-row">
          <span class="lbl" />
          <LoraListEditor label="LoRAs" :entries="form.loras" @change="form.loras = $event" />
        </div>

        <div class="grid-row">
          <label class="lbl" for="t2i-prompt">Prompt</label>
          <div class="stack">
            <textarea
              id="t2i-prompt"
              class="area"
              rows="7"
              spellcheck="false"
              :value="shownPrompt"
              :readonly="randomLocked"
              :placeholder="randomLocked ? 'A prompt is written for each step…' : 'The text the image model receives. Write it here, or press Generate Prompt to have it written from the caption.'"
              @input="form.prompt = ($event.target as HTMLTextAreaElement).value"
            />
            <ul v-if="promptWarnings.length || stepWarnings.length" class="lint">
              <li v-for="w in [...promptWarnings, ...stepWarnings]" :key="w">⚠ {{ w }}</li>
            </ul>
            <template v-if="hasNegative">
              <div>
                <button
                  type="button"
                  class="link-btn fold"
                  :aria-expanded="negativeOpen"
                  @click="negativeOpen = !negativeOpen"
                >{{ negativeOpen ? '▾' : '▸' }} Negative prompt{{ shownNegative.trim() ? ' •' : '' }}</button>
              </div>
              <textarea
                v-if="negativeOpen"
                class="area"
                rows="3"
                spellcheck="false"
                aria-label="Negative prompt"
                :value="shownNegative"
                :readonly="randomLocked"
                @input="form.negative = ($event.target as HTMLTextAreaElement).value"
              />
            </template>
          </div>
        </div>
      </fieldset>

      <div class="grid-row">
        <span class="lbl" />
        <div class="footer">
          <div class="actions">
            <button type="button" class="btn primary" :disabled="blocker !== null" @click="onGenerate">
              {{ submitting ? 'Submitting…' : generateLabel }}
            </button>
            <button
              type="button"
              class="btn danger"
              :disabled="!store.isBusy || cancelling"
              :title="store.isBusy ? 'Stop the running batches and drop their queued jobs' : 'Nothing is running'"
              @click="onCancel"
            >{{ cancelling ? 'Cancelling…' : 'Cancel' }}</button>
            <span v-if="blocker !== null && blocker !== 'Still loading'" class="hint">{{ blocker }}</span>
          </div>
          <div class="counts">
            <label
              class="fld fld-inline"
              title="Random Caption: how many captions to draw. Manual always renders one prompt."
            >
              <span>Batch Size</span>
              <input
                type="number"
                min="1"
                :max="maxBatchSize"
                step="1"
                :value="shownBatchSize"
                :disabled="form.mode === 'manual'"
                @change="onBatchSize"
              />
            </label>
            <label
              class="fld fld-inline"
              :class="{ 'fld-off': countIgnored }"
              :title="countIgnored ? 'Ignored while the seed is Fixed: the same seed would render the same image again' : 'How many images to render for each prompt'"
            >
              <span>Count Per Batch</span>
              <input
                type="number"
                min="1"
                :max="maxCount"
                step="1"
                :value="form.countPerBatch"
                @change="onCountPerBatch"
              />
            </label>
          </div>
        </div>
      </div>

      <div class="grid-row">
        <span class="lbl" />
        <div class="stack">
          <p class="status" role="status" aria-live="polite">{{ store.statusText }}</p>
          <ul v-if="batchWarnings.length" class="lint">
            <li v-for="w in batchWarnings" :key="w">⚠ {{ w }}</li>
          </ul>
        </div>
      </div>

      <!-- Shown only while an image is selected: says where edits are going.
           With none selected the form is an unsaved scratch area. -->
      <div v-if="store.selectedImage" class="bar" :class="{ failed: saveFailed }" role="status">
        <span v-if="saveFailed">
          Couldn't save your changes to this image — they'll be retried on the next edit.
        </span>
        <span v-else>
          Editing the image from {{ formatT2iTimestamp(store.selectedImage.created_at) }} ·
          changes save automatically
        </span>
        <button
          type="button"
          class="link-btn"
          title="Keep these values in the form, but stop saving them to this image"
          @click="stopEditing"
        >Stop editing</button>
      </div>
      <p v-if="store.imagesError" class="lint" role="alert">
        ⚠ Couldn't load your images: {{ store.imagesError }}
        <button type="button" class="link-btn" @click="store.refreshImages()">Retry</button>
      </p>

      <ThumbStrip
        :items="stripItems"
        :selected-id="store.selectedId"
        empty-text="No images yet — generated images appear here."
        :loading="!store.imagesLoaded || store.imagesLoading || store.isBusy"
        :has-more="store.hasMoreImages"
        :loading-more="store.loadingOlder"
        @select="onSelectTile"
        @open="onOpenViewer"
        @favorite="onFavorite"
        @delete="onDelete"
        @more="store.loadOlder()"
      >
        <template #jobs>
          <JobTile
            v-for="[jobId, chip] in store.jobs"
            :key="`job-${jobId}`"
            :chip="chip"
            @cancel="onCancelJob(jobId)"
            @dismiss="store.dismissJob(jobId)"
          />
        </template>
      </ThumbStrip>
    </div>

    <MediaViewer
      v-if="viewerIndex !== null"
      :media-list="viewerImages"
      :initial-index="viewerIndex"
      :allow-destructive="false"
      @close="viewerIndex = null"
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

.t2i-card {
  background: var(--surface-section);
  border-radius: 12px;
  padding: 20px 26px 22px;
  width: min(1080px, 96vw);
  max-height: 94vh;
  overflow-y: auto;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.t2i-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.t2i-header h3 {
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

.icon-btn:hover:not(:disabled) {
  color: var(--text-color);
}

.icon-btn:disabled {
  opacity: 0.5;
  cursor: default;
}

.dice {
  font-size: 18px;
}

/* A fieldset only to switch every control off at once while the dialog loads. */
.t2i-form {
  border: 0;
  padding: 0;
  margin: 0;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

/* Label column, then the content (the wireframe's layout). */
.grid-row {
  display: grid;
  grid-template-columns: 84px minmax(0, 1fr);
  column-gap: 12px;
  align-items: start;
}

.lbl {
  padding-top: 6px;
  text-align: right;
  font-size: 12px;
  color: var(--text-color-secondary);
}

.stack {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.area {
  width: 100%;
  font-family: inherit;
  font-size: 13px;
  line-height: 1.4;
  color: var(--text-color);
  background: var(--surface-ground);
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  padding: 6px 8px;
  box-sizing: border-box;
  resize: vertical;
}

.area:read-only {
  color: var(--text-color-secondary);
}

.area:focus {
  outline: none;
  border-color: var(--primary-color);
}

.resolved-text {
  font-family: var(--font-mono, monospace);
  font-size: 12px;
}

.resolved-text.stale {
  opacity: 0.55;
}

.caption-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px 14px;
}

.radio {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 13px;
  color: var(--text-color);
  cursor: pointer;
}

.filter-anchor {
  position: relative;
  margin-left: auto;
}

.filter-pop {
  position: fixed;
  z-index: 20;
}

.badge {
  margin-left: 6px;
  min-width: 18px;
  padding: 0 6px;
  border-radius: 9px;
  background: var(--primary-color);
  color: var(--primary-color-text, #fff);
  font-size: 11px;
  line-height: 18px;
  display: inline-block;
  text-align: center;
}

.params {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: flex-start;
}

.fld {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: var(--text-color-secondary);
}

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

.fld select:disabled,
.fld input:disabled {
  opacity: 0.6;
}

.fld-off {
  opacity: 0.55;
}

.hint {
  color: var(--text-color-secondary);
  font-size: 11px;
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

.btn:disabled {
  opacity: 0.6;
  cursor: default;
}

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

.footer {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  justify-content: space-between;
  gap: 10px 24px;
}

.actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.counts {
  display: flex;
  gap: 16px;
}

.fld-inline {
  flex-direction: row;
  align-items: center;
  gap: 8px;
}

.fld-inline input {
  width: 72px;
}

.status {
  min-height: 1.4em;
  margin: 0;
  font-size: 12px;
  color: var(--text-color-secondary);
}

.note {
  margin: 0;
  color: var(--text-color-secondary);
  font-size: 12px;
}

.lint {
  margin: 0;
  padding-left: 0;
  list-style: none;
  color: var(--warn, #c90);
  font-size: 12px;
}

.bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 6px 10px;
  border-radius: 6px;
  font-size: 12px;
  color: var(--text-color-secondary);
  background: var(--surface-ground);
  border: 1px solid var(--surface-border);
  border-left: 3px solid var(--primary-color, #6366f1);
}

.bar.failed {
  border-left-color: #c33;
  color: #c33;
}

.link-btn {
  background: none;
  border: none;
  padding: 0;
  cursor: pointer;
  font-size: 12px;
  color: var(--primary-color, #6366f1);
  white-space: nowrap;
}

.link-btn:hover:not(:disabled) {
  text-decoration: underline;
}

.link-btn:disabled {
  opacity: 0.5;
  cursor: default;
}

.fold {
  color: var(--text-color-secondary);
}
</style>
