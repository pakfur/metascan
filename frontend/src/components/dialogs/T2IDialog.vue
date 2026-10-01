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
  toThumbItem,
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
  firstBatchSource,
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
import ThumbGrid from '../generation/ThumbGrid.vue'
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
// read-only live views of it. The server decides what to show: the batch being
// rendered (its caption, prompt and aspect stay put while its images render) and
// the seed of the image being rendered, which moves on after each image. They are
// a display only: the form itself is untouched until the batch ends, when the
// last batch's values are copied in (a finished batch hands its values over
// exactly once).
const randomLocked = computed(() => store.randomBatch !== null)
const step = computed(() => (randomLocked.value ? store.currentStep : null))
// A run whose first batch is already known to the dialog (the user's own prompt,
// or their caption to write a prompt for) has nothing to wait for. From the moment
// Generate is pressed until the server's batch_step frame arrives (milliseconds
// with a prompt, the model's time without) the locked boxes keep showing the
// form's values instead of going blank and filling in again. Only while no step
// has arrived: a later step's null negative must not fall back to the form.
const supplyingFirstStep = ref(false)
const holdForm = computed(() => randomLocked.value && step.value === null && supplyingFirstStep.value)
const liveBoxes = computed(() => randomLocked.value && !holdForm.value)
const shownCaption = computed(() => (liveBoxes.value ? (step.value?.caption ?? '') : form.caption))
const shownPrompt = computed(() => (liveBoxes.value ? (step.value?.prompt ?? '') : form.prompt))
const shownNegative = computed(() => (liveBoxes.value ? (step.value?.negative ?? '') : form.negative))
const shownAspect = computed(() => (step.value ? step.value.aspect_ratio : form.aspect))
// The seed box always holds the seed the next image will use. While a Random run
// renders that is the image being rendered; the form's own seed was advanced to
// the next unused one when Generate was pressed, so it is right the moment the
// boxes unlock, and after a reload or a dialog closed mid-run.
const shownSeed = computed(() => (randomLocked.value ? (store.liveSeed ?? form.seed) : form.seed))
const stepWarnings = computed(() => step.value?.warnings ?? [])

// The flag means exactly "waiting for the first step's frame": it ends when a
// step arrives or the lock ends (the start failing is handled in onGenerate).
watch(randomLocked, (locked) => {
  if (!locked) supplyingFirstStep.value = false
})
watch(step, (s) => {
  if (s) supplyingFirstStep.value = false
})

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
    form.prompt = r.prompt // a Random Generate renders it as its first batch
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

// The dice loads one caption (and its aspect ratio) from the caption file so it
// can be read and edited before anything is rendered. It empties the Prompt (and
// its negative): they belong to the caption it replaces, and an empty Prompt is
// what makes Generate write one for this caption instead of rendering the old.
async function onRoll() {
  if (rolling.value) return
  const target = store.selectedId
  rolling.value = true
  try {
    const row = await store.rollCaption(form.filter)
    if (store.selectedId !== target) return // another image was loaded meanwhile
    form.caption = row.caption
    form.prompt = ''
    form.negative = ''
    promptWarnings.value = []
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
// and no taller than the room below it: inside the form (which scrolls and
// clips) its footer, where the match count is, would be cut off.
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

// Random Caption mode: the boxes alone say what the first batch is (the Prompt
// box, rendered as it stands; else the Caption box, which has a prompt written
// for it; else a rolled caption). The batches after it always roll a caption
// and write a prompt.
const firstBatch = computed(() => firstBatchSource(form))
// Says what Generate will do, from the same boxes that decide it.
const firstBatchHint = computed(() => {
  const more = shownBatchSize.value > 1
  switch (firstBatch.value) {
    case 'prompt':
      return more
        ? 'Batch 1 renders the prompt above; the others roll new captions and write their prompts'
        : 'Renders the prompt above'
    case 'caption':
      return more
        ? 'Batch 1 writes a prompt for the caption above; the others roll new captions'
        : 'Writes a prompt for the caption above, then renders it'
    default:
      return more
        ? 'Every batch rolls a new caption and writes its prompt'
        : 'Rolls a caption, writes its prompt, then renders it'
  }
})

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
  // Set before the request goes out: the batch's WebSocket frames can beat the response.
  supplyingFirstStep.value = req.mode === 'random' && (!!req.prompt || !!req.caption)
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
    // The seed box then holds the next unused seed, for both modes (unless the
    // user already changed it). A Random run shows the seed of the image being
    // rendered meanwhile (shownSeed), and this value is what is there when the
    // boxes unlock, whether or not the dialog was open to see the run end.
    if (r.next_seed !== null && form.seed === req.seed) form.seed = r.next_seed
    toast.show(
      r.total_images === 1 ? 'Image job queued' : `${r.total_images} image jobs queued`,
      'success',
    )
  } catch (e) {
    supplyingFirstStep.value = false
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

// ---- the image grid: select, favourite, delete, view ----------------------------------------------------------
// Whole pages of the images held, newest first (the store's window over them).
const gridItems = computed(() => store.shownImages.map(toThumbItem))

// Clicking an image loads its saved form_state into the form and starts
// autosaving into it. Seed policy, Batch Size and Count per Batch stay as they
// are, and nothing is started (a stored Random mode only restores the mode
// and its filter). The grid only reports the FIRST click of a double-click,
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

// The viewer gets a snapshot of what the grid shows, taken when it opens:
// images arriving mid-batch would otherwise shift the list under its index.
const viewerImages = shallowRef<Media[]>([])
const viewerIndex = ref<number | null>(null)
function onOpenViewer(index: number) {
  viewerImages.value = store.shownImages.map(t2iImageToMedia)
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
       is the only way out. Scroll events do not bubble, so the card listens in
       the capture phase: the fixed Filter panel follows its button whichever
       box scrolls. -->
  <div class="dialog-overlay">
    <div
      class="t2i-card"
      role="dialog"
      aria-modal="true"
      aria-labelledby="t2i-title"
      @scroll.capture="filterOpen && placeFilter()"
    >
      <header class="t2i-header">
        <h3 id="t2i-title">Text to Image</h3>
        <button type="button" class="icon-btn" title="Close" aria-label="Close" @click="close()">✕</button>
      </header>

      <div class="t2i-body">
        <!-- The form column scrolls as one box, so everything in it shares one
             right edge. The bars stick to its top and the run controls to its
             bottom. The editing bar shows only while an image is selected: it
             says where edits are going; with none selected the form is an
             unsaved scratch area. -->
        <div class="t2i-main">
          <div class="t2i-top">
            <div v-if="!store.config && store.configError" class="bar failed" role="alert">
              <span>Couldn't load the Text to Image settings: {{ store.configError }}</span>
              <button type="button" class="link-btn" @click="retryLoad">Retry</button>
            </div>
            <p v-else-if="!ready" class="note">Loading…</p>
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
          </div>

          <fieldset class="t2i-form" :disabled="!ready">
            <section class="sec">
              <div class="sec-head">
                <label class="sec-title" for="t2i-caption">Caption</label>
                <div class="source">
                  <div class="seg" role="radiogroup" aria-label="Caption source">
                    <label class="seg-opt">
                      <input v-model="form.mode" type="radio" name="t2i-mode" value="manual" :disabled="randomLocked" />
                      <span>Manual</span>
                    </label>
                    <label class="seg-opt" :title="csvAvailable ? '' : (store.config?.csv.error ?? 'The caption file is not available')">
                      <input
                        v-model="form.mode"
                        type="radio"
                        name="t2i-mode"
                        value="random"
                        :disabled="randomLocked || !csvAvailable"
                      />
                      <span>Random Caption</span>
                    </label>
                  </div>
                  <button
                    type="button"
                    class="icon-btn dice"
                    title="Load a random caption from the caption file (this empties the prompt, so Generate writes one for it)"
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
              <textarea
                id="t2i-caption"
                class="area"
                rows="3"
                spellcheck="false"
                :value="shownCaption"
                :readonly="randomLocked"
                :placeholder="randomLocked ? 'A caption is drawn from the caption file for each batch…' : 'Describe the image. Character tokens like __ALICE__ and __HAIR__ are filled in for you.'"
                @input="form.caption = ($event.target as HTMLTextAreaElement).value"
              />
              <div class="sec-foot">
                <button
                  type="button"
                  class="link-btn fold"
                  :aria-expanded="resolvedOpen"
                  :disabled="randomLocked"
                  @click="toggleResolved"
                >{{ resolvedOpen ? '▾' : '▸' }} Resolved caption</button>
                <button
                  type="button"
                  class="btn"
                  :disabled="generating || randomLocked || !form.caption.trim() || !form.model"
                  @click="onGeneratePrompt"
                >{{ generating ? 'Writing prompt…' : 'Generate Prompt' }}</button>
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
            </section>

            <section class="sec">
              <label class="sec-title" for="t2i-prompt">Prompt</label>
              <textarea
                id="t2i-prompt"
                class="area"
                rows="6"
                spellcheck="false"
                :value="shownPrompt"
                :readonly="randomLocked"
                :placeholder="randomLocked ? 'A prompt is written for each batch…' : 'The text the image model receives. Write it here, or press Generate Prompt to have it written from the caption.'"
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
            </section>

            <!-- Two rows on a four-column grid: Model | Workflow, then
                 Size | Aspect Ratio | Seed. -->
            <section class="sec settings">
              <label class="fld f-model">
                <span>Model</span>
                <select :value="form.model" @change="onModelChange(($event.target as HTMLSelectElement).value)">
                  <option v-for="m in modelOptions" :key="m.id" :value="m.id">{{ m.label }}</option>
                </select>
              </label>
              <label class="fld f-workflow">
                <span>Workflow</span>
                <select v-model="form.presetId">
                  <option :value="null" disabled>Choose a workflow…</option>
                  <option v-if="presetMissing" :value="form.presetId" disabled>Workflow #{{ form.presetId }} (not found)</option>
                  <option v-for="p in presets" :key="p.id" :value="p.id">{{ p.name }}</option>
                </select>
                <small v-if="ready && !presets.length" class="hint">none registered: add one under Configuration</small>
              </label>
              <label class="fld f-size">
                <span>Size</span>
                <select v-model="form.megapixels">
                  <option v-for="m in sizeOptions" :key="m" :value="m">{{ m }} MP</option>
                </select>
              </label>
              <label class="fld f-aspect">
                <span>Aspect Ratio</span>
                <select
                  :value="shownAspect"
                  :disabled="randomLocked"
                  :title="form.mode === 'random' ? 'A Random batch takes the aspect ratio of each caption' : ''"
                  @change="form.aspect = ($event.target as HTMLSelectElement).value"
                >
                  <option v-for="a in aspectOptions" :key="a" :value="a">{{ a }}</option>
                </select>
                <small v-if="form.mode === 'random' && !randomLocked" class="hint">{{ firstBatch !== 'draw' ? 'used for batch 1, then taken from each caption' : 'taken from each caption' }}</small>
              </label>
              <SeedControls
                class="f-seed"
                :seed="shownSeed"
                :policy="form.seedPolicy"
                :disabled="randomLocked"
                :max="seedMax"
                @update:seed="form.seed = $event"
                @update:policy="form.seedPolicy = $event"
              />
            </section>

            <section class="sec">
              <LoraListEditor label="LoRAs" :entries="form.loras" @change="form.loras = $event" />
            </section>
          </fieldset>

          <footer class="t2i-run">
            <div class="run-row">
              <div class="counts">
                <label
                  class="fld fld-inline"
                  title="Random Caption: how many batches a run renders. Each batch is one caption, one prompt and Count Per Batch images; batch 1 uses the prompt above when there is one. Manual always renders one prompt."
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
              <div class="actions">
                <button
                  type="button"
                  class="btn danger"
                  :disabled="!store.isBusy || cancelling"
                  :title="store.isBusy ? 'Stop the running batches and drop their queued jobs' : 'Nothing is running'"
                  @click="onCancel"
                >{{ cancelling ? 'Cancelling…' : 'Cancel' }}</button>
                <button type="button" class="btn primary" :disabled="blocker !== null" @click="onGenerate">
                  {{ submitting ? 'Submitting…' : generateLabel }}
                </button>
              </div>
            </div>
            <!-- Left: what is running. Right, under Generate: what it will do, or why it can't. -->
            <div class="run-info">
              <p class="status" role="status" aria-live="polite">{{ store.statusText }}</p>
              <p v-if="blocker !== null && blocker !== 'Still loading'" class="hint">{{ blocker }}</p>
              <p v-else-if="form.mode === 'random' && !randomLocked" class="hint">{{ firstBatchHint }}</p>
            </div>
            <ul v-if="batchWarnings.length" class="lint">
              <li v-for="w in batchWarnings" :key="w">⚠ {{ w }}</li>
            </ul>
          </footer>
        </div>

        <!-- The images, newest first, a page of 60 at a time. -->
        <section class="t2i-gallery" aria-labelledby="t2i-images-title">
          <h4 id="t2i-images-title" class="sec-title">Images</h4>
          <p v-if="store.imagesError" class="lint" role="alert">
            ⚠ Couldn't load your images: {{ store.imagesError }}
            <button type="button" class="link-btn" @click="store.refreshImages()">Retry</button>
          </p>
          <ThumbGrid
            class="gallery-grid"
            :items="gridItems"
            :selected-id="store.selectedId"
            empty-text="No images yet — generated images appear here."
            :loading="!store.imagesLoaded || store.imagesLoading || store.isBusy"
            :has-more="store.canShowMore"
            :loading-more="store.loadingOlder"
            @select="onSelectTile"
            @open="onOpenViewer"
            @favorite="onFavorite"
            @delete="onDelete"
            @more="store.showMoreImages()"
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
          </ThumbGrid>
        </section>
      </div>
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

/* Two panes: the form and the image grid, which scrolls on its own. The form
   decides the height (the grid takes whatever the form leaves it), up to 94%
   of the viewport; past that the form column scrolls under its pinned run
   controls. */
.t2i-card {
  background: var(--surface-section);
  border-radius: 12px;
  padding: 16px 22px 18px;
  width: min(1180px, 96vw);
  min-height: min(560px, 94vh);
  max-height: 94vh;
  overflow: hidden;
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

.t2i-body {
  flex: 1 1 auto;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  grid-template-rows: minmax(0, 1fr);
  gap: 20px;
}

/* The gutter is reserved so nothing changes width when a fold opens and the
   column starts to scroll. */
.t2i-main {
  min-width: 0;
  min-height: 0;
  overflow-y: auto;
  scrollbar-gutter: stable;
  padding-right: 6px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.t2i-top {
  position: sticky;
  top: 0;
  z-index: 2;
  display: flex;
  flex-direction: column;
  gap: 8px;
  background: var(--surface-section);
}

.t2i-top:empty {
  display: none;
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
  line-height: 1;
}

/* A fieldset only to switch every control off at once while the dialog loads. */
.t2i-form {
  flex: 1 0 auto;
  border: 0;
  padding: 0;
  margin: 0;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.sec {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

/* Same look as the LoRA editor's label, so every section reads alike. */
.sec-title {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.4px;
  color: var(--text-color-secondary);
}

.sec-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 6px 12px;
}

.sec-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.source {
  display: flex;
  align-items: center;
  gap: 8px;
}

/* Manual | Random Caption as one segmented control. The radios stay real and
   focusable (arrow keys switch); only their circles are hidden. */
.seg {
  display: inline-flex;
  gap: 2px;
  padding: 2px;
  border: 1px solid var(--surface-border);
  border-radius: 7px;
  background: var(--surface-ground);
}

.seg-opt {
  position: relative;
  display: inline-flex;
}

.seg-opt input {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  margin: 0;
  opacity: 0;
  cursor: pointer;
}

.seg-opt span {
  padding: 4px 12px;
  border-radius: 5px;
  font-size: 12px;
  line-height: 18px;
  color: var(--text-color-secondary);
  white-space: nowrap;
}

.seg-opt:hover input:not(:disabled):not(:checked) + span {
  color: var(--text-color);
}

.seg-opt input:checked + span {
  background: var(--surface-section);
  color: var(--primary-color);
  font-weight: 600;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.15);
}

.seg-opt input:focus-visible + span {
  outline: 2px solid var(--primary-color);
  outline-offset: 1px;
}

.seg-opt input:disabled {
  cursor: default;
}

.seg-opt input:disabled + span {
  opacity: 0.5;
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

.resolved {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.resolved-text {
  font-family: var(--font-mono, monospace);
  font-size: 12px;
}

.resolved-text.stale {
  opacity: 0.55;
}

.filter-anchor {
  position: relative;
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

.settings {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  padding-top: 16px;
  border-top: 1px solid var(--surface-border);
}

.f-model,
.f-workflow,
.f-seed {
  grid-column: span 2;
}

.fld {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
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

.fld select {
  width: 100%;
}

.fld select:focus,
.fld input:focus,
.f-seed :deep(.sc-input:focus),
.f-seed :deep(.sc-policy:focus) {
  outline: none;
  border-color: var(--primary-color);
}

.fld select:disabled,
.fld input:disabled {
  opacity: 0.6;
}

/* The seed box takes the room its cell has left after the dice and the policy. */
.f-seed :deep(.sc-row) {
  display: flex;
}

.f-seed :deep(.sc-input) {
  flex: 1 1 auto;
  width: auto;
  min-width: 0;
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
  white-space: nowrap;
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

/* Grows no taller than its content; the form above it fills the rest, so
   the run controls sit at the bottom even when the fields are short. */
.t2i-run {
  position: sticky;
  bottom: 0;
  z-index: 2;
  flex: none;
  background: var(--surface-section);
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding-top: 12px;
  border-top: 1px solid var(--surface-border);
}

.run-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 10px 20px;
}

.counts {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 16px;
}

.actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-left: auto;
}

.fld-inline {
  flex-direction: row;
  align-items: center;
  gap: 8px;
}

.fld-inline input {
  width: 72px;
}

.run-info {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  justify-content: space-between;
  gap: 2px 16px;
  min-height: 1.4em;
}

.run-info .hint {
  margin-left: auto;
  text-align: right;
}

.status {
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

/* The image pane: as wide as three tiles (ThumbGrid sizes itself) and as tall
   as the form. Height 0 keeps its tiles from making the row taller; the
   min-height then stretches it over the row the form sets. */
.t2i-gallery {
  height: 0;
  min-height: 100%;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 4px 0 12px;
  background: var(--surface-ground);
  border: 1px solid var(--surface-border);
  border-radius: 10px;
}

.gallery-grid {
  flex: 1 1 auto;
}

/* A narrower form: the settings grid drops to two columns. */
@media (max-width: 1100px) {
  .settings {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .f-model,
  .f-workflow {
    grid-column: span 1;
  }
}

/* Too narrow for two panes: one column with the images below the form (still
   three wide), and the whole card scrolls. Nothing in it may shrink to fit the
   card's height, or the run controls and the images would overlap the form. */
@media (max-width: 899px) {
  .t2i-card {
    height: auto;
    max-height: 94vh;
    overflow-y: auto;
  }

  .t2i-body,
  .t2i-main {
    flex: none;
    min-height: auto;
  }

  .t2i-body {
    display: flex;
    flex-direction: column;
  }

  .t2i-main {
    overflow: visible;
    padding-right: 0;
  }

  .t2i-gallery {
    flex: none;
    align-self: center;
    height: 440px;
    min-height: 0;
  }
}
</style>
