<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, shallowRef, watch } from 'vue'
import { onClickOutside, useDebounceFn, useEventListener } from '@vueuse/core'
import {
  DEFAULT_MAX_BATCH_SIZE,
  DEFAULT_MAX_COUNT_PER_BATCH,
  MAX_VARIANTS,
  SEED_MAX,
  T2I_WRITERS,
  WRITER_STORAGE_KEY,
  activeFilterCount,
  effectiveBatchSize,
  effectiveCount,
  formatT2iTimestamp,
  isCountEffective,
  multiVariantGroups,
  randomSeed,
  SCORECARD_BLIND_TEXT,
  t2iImageToMedia,
  toThumbItem,
  withCurrentOption,
  type T2iFormState,
  type T2iWriter,
} from '../../types/t2i'
import type { ThumbItem } from '../../types/jobs'
import {
  ADAPTER_LOADING,
  ADAPTER_LOADING_TEXT,
  ADAPTER_UNAVAILABLE,
  adapterErrorCode,
  adapterErrorWriter,
  adapterMenuOptions,
  builtinWriterLabel,
  isAdapterWriterId,
  servableAdapterIds,
  writerName,
} from '../../types/writers'
import type { Media } from '../../types/media'
import type { WorkflowPreset } from '../../types/storyboard'
import { ApiError } from '../../api/client'
import { listPresets } from '../../api/comfy'
import { fetchCaptionTokens, getT2iPendingJob } from '../../api/t2i'
import { t2iWriterLabel, type CaptionToken, type T2iPendingJob } from '../../types/t2i'
import TextEditPopup from '../storyboard/TextEditPopup.vue'
import { usePendingJob } from '../../composables/usePendingJob'
import { jobStateText } from '../../types/jobs'
import TokenTextarea from '../generation/TokenTextarea.vue'
import { useFoldersStore } from '../../stores/folders'
import { useT2iStore } from '../../stores/t2i'
import { useTasteStore } from '../../stores/taste'
import { useTrainingStore } from '../../stores/training'
import { useFormAutosave } from '../../composables/useFormAutosave'
import { isUnknownCategoryError, useGenerationCategories } from '../../composables/useGenerationCategories'
import { useToast } from '../../composables/useToast'
import { confirmLeave } from '../../utils/unsavedForm'
import { useWebSocket } from '../../composables/useWebSocket'
import { useWriterPick } from '../../composables/useWriterPick'
import { GPU_BUSY_TEXT, isGpuBusyTraining } from '../../utils/gpuBusy'
import {
  buildBatchRequest,
  effectivePromptCount,
  promptsLocked,
  writesPromptsFirst,
  defaultPresetFor,
  draftSetFromImage,
  draftSetFromResult,
  draftSetFromSteps,
  firstBatchSource,
  generateBlocker,
  initialFields,
  mergeFormState,
  plannedSteps,
  requestSignature,
  savableForm,
  toFormState,
  unlockPatch,
  usableDraft,
  usablePrompts,
  type BatchContext,
  type T2iFields,
} from '../../utils/t2iDialogForm'
import CaptionFilterPopover from '../t2i/CaptionFilterPopover.vue'
import CategorySelect from '../generation/CategorySelect.vue'
import JobTile from '../generation/JobTile.vue'
import SeedControls from '../generation/SeedControls.vue'
import T2iImageInfo from '../generation/T2iImageInfo.vue'
import ThumbGrid from '../generation/ThumbGrid.vue'
import LoraListEditor from '../storyboard/LoraListEditor.vue'
import MediaViewer from '../viewer/MediaViewer.vue'

const emit = defineEmits<{ close: [] }>()

const store = useT2iStore()
const taste = useTasteStore()
const training = useTrainingStore()
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

// The form as it last matched a render or a loaded image, as a plain string:
// with no image selected, a form that differs from it holds changes nothing
// has generated, and loading a tile would lose them. Never a ref or a watcher
// source, and nothing here writes the form.
let cleanForm = ''
const snapshotForm = () => JSON.stringify(toFormState(form))

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
  // A restored draft compares with the defaults, and its seed alone does not count.
  cleanForm = JSON.stringify(toFormState({ ...fresh, seed: form.seed }))
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
  // Drafts are written in the model's prompt style: another model's are
  // let go (the box keeps its text, which then goes out as typed).
  store.discardDrafts()
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

// ---- a placeholder tile, shown in the form --------------------------------------------------------
// A click on a job tile shows what that image was submitted with in the form's
// own boxes, read-only, the way a Random run shows its batch: the boxes display
// the job (the shown* values below) and the form itself is never written. So
// nothing autosaves into a selected image, the scratch draft is untouched, and
// the form is exactly as it was on "Back to the form", a tile click or the
// tile leaving (the job finished, was cancelled or dismissed).
const pending = usePendingJob(() => store.jobs, getT2iPendingJob)
const viewingJob = computed(() => pending.selectedJobId.value !== null)
const job = computed<T2iPendingJob | null>(() => (viewingJob.value ? pending.info.value : null))

const jobModel = computed(() => {
  const id = job.value?.model
  return id ? (models.value.find((m) => m.id === id)?.label ?? id) : 'unknown'
})
const jobWorkflow = computed(() => {
  const id = job.value?.preset_id
  return id == null ? 'unknown' : (presets.value.find((p) => p.id === id)?.name ?? `Workflow #${id}`)
})
const jobSize = computed(() => {
  const j = job.value
  if (!j || j.megapixels == null) return 'unknown'
  return j.width && j.height ? `${j.megapixels} MP · ${j.width}×${j.height}` : `${j.megapixels} MP`
})
const jobViewText = computed(() => {
  if (pending.error.value) return pending.error.value
  if (pending.loading.value) return 'Loading the queued image…'
  const j = job.value
  const parts = [`Queued image · ${jobStateText(pending.chip.value)}`]
  if (j?.created_at) parts.push(`queued ${formatT2iTimestamp(j.created_at)}`)
  if (j?.writer_id) {
    const who = t2iWriterLabel(j.writer_id, store.adaptersLoaded ? store.adapters : null) ?? j.writer_id
    parts.push(`written by ${who}${j.variant_index != null ? ` · prompt ${j.variant_index + 1}` : ''}`)
  }
  if (j && j.caption === null && j.model === null) parts.push('caption and model not kept (server restarted)')
  parts.push('read-only')
  return parts.join(' · ')
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
// Every box read-only: a Random run holds them, or a job tile is on show.
const locked = computed(() => randomLocked.value || viewingJob.value)
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
const shownCaption = computed(() =>
  viewingJob.value ? (job.value?.caption ?? '') : liveBoxes.value ? (step.value?.caption ?? '') : form.caption,
)
const shownPrompt = computed(() =>
  viewingJob.value ? (job.value?.prompt ?? '') : liveBoxes.value ? (step.value?.prompt ?? '') : form.prompt,
)
const shownNegative = computed(() =>
  viewingJob.value ? (job.value?.negative ?? '') : liveBoxes.value ? (step.value?.negative ?? '') : form.negative,
)
const shownAspect = computed(() =>
  viewingJob.value ? (job.value?.aspect_ratio ?? '') : step.value ? step.value.aspect_ratio : form.aspect,
)
// The seed box always holds the seed the next image will use. While a Random run
// renders that is the image being rendered; the form's own seed was advanced to
// the next unused one when Generate was pressed, so it is right the moment the
// boxes unlock, and after a reload or a dialog closed mid-run.
const shownSeed = computed(() =>
  viewingJob.value
    ? (job.value?.seed ?? form.seed)
    : randomLocked.value
      ? (store.liveSeed ?? form.seed)
      : form.seed,
)
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
    if (patch) {
      const before = toFormState(form)
      Object.assign(form, mergeFormState(form, patch))
      // The hand-back is a render's result, not an edit: move the baseline by
      // what the patch changed and no more, so edits made during the run (the
      // boxes that stay unlocked) are still unsaved.
      if (cleanForm) {
        const after = toFormState(form)
        const clean = JSON.parse(cleanForm) as Record<string, unknown>
        for (const k of Object.keys(after) as (keyof T2iFormState)[]) {
          if (JSON.stringify(after[k]) !== JSON.stringify(before[k])) clean[k] = after[k]
        }
        cleanForm = JSON.stringify(clean)
      }
    }
    // The handed-back prompt is the writer's draft (one of its caption's
    // variants): kept with it, so the next Generate records that writer and
    // whether the text was edited, and ◀ ▶ reaches its siblings. Captured
    // here, with the patch that fills the box, and nowhere else.
    if (patch && patch.prompt !== undefined && finished.step) {
      store.setDrafts(draftSetFromSteps(finished.drafts, finished.step))
    }
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

// Caption directions: remembered per browser, not per image (nothing about
// them is stored with an image). Defaults to the config's switch.
const DIRECTIONS_KEY = 'metascan.t2i.directions.v1'
function readDirectionsPref(): boolean | null {
  try {
    const v = localStorage.getItem(DIRECTIONS_KEY)
    return v === null ? null : v === '1'
  } catch {
    return null
  }
}
const directionsPref = ref<boolean | null>(readDirectionsPref())
const useDirections = computed(() => directionsPref.value ?? store.config?.directions?.enabled ?? true)
function setDirections(on: boolean) {
  directionsPref.value = on
  try {
    localStorage.setItem(DIRECTIONS_KEY, on ? '1' : '0')
  } catch {
    /* private mode: the choice lasts for this session */
  }
}

// Categories every image of the next batch is tagged with. Session-local, like
// the directions switch: not part of form_state, so selecting a tile never
// changes it, and no watcher here touches the form.
const { selected: categoryIds, refresh: refreshCategories } = useGenerationCategories(
  'metascan.t2i.categories.v1',
)

// Prompt-writer adapters after Base / Base + taste: the T2I ones scoped to
// `all` or to a selected category, labelled with their pass rate on the
// blind scorecard (Base with its baseline rate); one with too few examples
// for the selected model is listed disabled.
const adapterOptions = computed(() =>
  adapterMenuOptions(store.adapters, {
    kind: 't2i',
    categoryIds: categoryIds.value,
    targetModel: form.model || null,
    scorecards: training.scorecards,
  }),
)
const builtinOptions = computed(() =>
  T2I_WRITERS.map((w) => ({ id: w.id, label: builtinWriterLabel(w.id, w.label, training.scorecards) })),
)
useWebSocket('training', (event) => {
  if (event === 'adapters_changed') {
    writerPick.clearRefusal()
    void store.loadAdapters()
    training.scheduleScorecards()
  } else if (event === 'scorecard_judged' || event === 'scorecard_complete') {
    training.scheduleScorecards()
  }
})
onMounted(() => {
  training.loadScorecards().catch(() => {}) // labels only
})

// The Writer pick (see useWriterPick): the user's choice survives a transient
// fallback to Base and comes back when its adapter is offered again; only a
// deleted adapter drops it. Session-local, never part of the form, so a
// fallback touches no image.
const writerPick = useWriterPick<T2iWriter>({
  storageKey: WRITER_STORAGE_KEY,
  builtinIds: T2I_WRITERS.map((w) => w.id),
  adapters: () => store.adapters,
  adaptersLoaded: () => store.adaptersLoaded,
  options: () => adapterOptions.value,
})
const writer = writerPick.writer
const setWriter = writerPick.setWriter

// True when `e` was an adapter refusal, after dealing with it: a refused
// adapter falls back to Base (the pick is kept for when it returns); a new
// one the busy writer has not loaded yet keeps the choice and says to try
// again.
function handledAdapterError(e: unknown): boolean {
  const code = adapterErrorCode(e)
  if (code === ADAPTER_LOADING) {
    toast.show(ADAPTER_LOADING_TEXT, 'info', 6000)
    return true
  }
  if (code === ADAPTER_UNAVAILABLE) {
    // The 409 names the adapter: the Writer pick falls back only when it is
    // the one refused; a held draft by it is dropped instead (the box keeps
    // its text, which the next Generate sends as typed).
    const refused = adapterErrorWriter(e) ?? writer.value
    if (refused === writer.value && isAdapterWriterId(refused)) {
      writerPick.refuse(refused)
    } else if (store.drafts?.entries.some((d) => d.writer_id === refused)) {
      store.discardDrafts()
      const name = writerPick.nameOf(refused)
      toast.show(
        `The prompt's writer ${name} is no longer available; Generate again to send it as typed.`,
        'warn',
        6000,
      )
    }
    void store.loadAdapters()
    return true
  }
  return false
}

// The direction the prompt in the box was written with (Generate Prompt), or
// the running step's while a Random batch has the boxes. Display only.
const promptDirection = ref<{ text: string; parts: string[] } | null>(null)
const shownDirection = computed(() => {
  if (viewingJob.value) return null
  if (liveBoxes.value) {
    const s = step.value
    return s?.direction ? { text: s.direction, parts: s.direction_parts } : null
  }
  return promptDirection.value
})
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
// Writes PROMPTS prompts; true when they went into the boxes (a Manual
// Generate with only a caption calls it first, then renders them).
async function onGeneratePrompt(): Promise<boolean> {
  if (!form.caption.trim() || !form.model || generating.value) return false
  const body = {
    caption: form.caption,
    seed: form.seed,
    model: form.model,
    directions: useDirections.value,
    variants: promptCount.value,
    writer: writer.value,
    category_ids: [...categoryIds.value],
  }
  const target = store.selectedId
  generating.value = true
  try {
    const r = await store.generatePrompt(body)
    // The drafts are for this caption and model only. Another image loaded
    // meanwhile would get them autosaved into it; an edited caption would
    // drop them on the next Generate and send the box as a typed prompt; a
    // different model would install drafts written in another model's style.
    if (store.selectedId !== target || form.caption !== body.caption || form.model !== body.model) {
      toast.show(
        'The prompt was ready, but the image, caption or model changed meanwhile, so it was not used.',
        'info',
        4000,
      )
      return false
    }
    form.prompt = r.prompt // a Random Generate renders it as its first batch
    if (hasNegative.value && r.negative !== null) form.negative = r.negative
    // Every draft is held in the store for the caption it was written for;
    // the box shows variant 1.
    store.setDrafts(
      draftSetFromResult(body.caption, r.drafts ?? [], {
        resolved_caption: r.resolved_caption,
        direction: r.direction,
        categories: r.categories ?? [],
      }),
    )
    promptWarnings.value = r.warnings
    promptDirection.value = r.direction ? { text: r.direction, parts: r.direction_parts } : null
    resolved.value = { key: JSON.stringify([body.caption, body.seed, body.model]), text: r.resolved_caption, warnings: r.warnings }
    resolvedOpen.value = true
    return true
  } catch (e) {
    if (handledAdapterError(e)) return false
    if (isGpuBusyTraining(e)) {
      toast.show(GPU_BUSY_TEXT, 'warn', 5000)
      return false
    }
    if (isUnknownCategoryError(e)) {
      void refreshCategories()
      toast.show(
        'A chosen category no longer exists. It was removed from Categories; check them and Generate Prompt again.',
        'warn',
        6000,
      )
      return false
    }
    toast.show(
      e instanceof ApiError && e.status === 502
        ? `The prompt writer failed (${e.message}). Press Generate Prompt to try again.`
        : `Couldn't write the prompt: ${errMsg(e)}`,
      'warn',
      6000,
    )
    return false
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
    store.discardDrafts()
    promptWarnings.value = []
    promptDirection.value = null
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

// Typing in the Caption box: drafts belong to the caption they were written
// for, so any change lets them go (the Prompt box keeps its text, which a
// Generate then sends as typed).
// The tokens the caption box completes after `__` (fetched once; none on a
// failure, which only costs the list).
const captionTokens = ref<CaptionToken[]>([])
onMounted(async () => {
  try {
    captionTokens.value = (await fetchCaptionTokens()).tokens
  } catch (e) {
    console.error('Failed to load caption tokens', e)
  }
})

function onCaptionInput(text: string) {
  form.caption = text
  if (store.drafts) store.discardDrafts()
}

// ---- the draft switcher ------------------------------------------------------------------------------
// "variant k of N ◀ ▶" over the drafts the store holds for the caption in the
// box. Shown only while they still belong to it and nothing has the boxes.
const draftSet = computed(() => {
  const set = store.drafts
  return set && !locked.value && set.caption === form.caption && set.entries.length > 1 ? set : null
})
const draftLabel = computed(() => {
  const set = draftSet.value
  if (!set) return ''
  const entry = set.entries[set.current]
  return `prompt ${set.current + 1} of ${set.entries.length} · ${writerName(store.adapters, entry.writer_id)}`
})
// An explicit user action: what the boxes hold stays with the variant being
// left, and the other variant's text (as last left) goes in.
function switchVariant(delta: number) {
  const set = draftSet.value
  if (!set) return
  const next = store.switchDraft(set.current + delta, form.prompt, form.negative)
  if (!next) return
  form.prompt = next.prompt
  if (hasNegative.value) form.negative = next.negative
}

// ---- the token help ----------------------------------------------------------------------------------
// The "?" beside the Caption title: what each __TOKEN__ becomes (docs/t2i.md,
// Tokens). The names are the shipped characters.yml defaults.
const tokenHelpOpen = ref(false)
const tokenHelpAnchor = ref<HTMLElement | null>(null)
onClickOutside(tokenHelpAnchor, () => {
  tokenHelpOpen.value = false
})
const TOKEN_HELP: { token: string; meaning: string }[] = [
  { token: '__ALICE__', meaning: 'Replaced with a random description of the first female subject' },
  { token: '__BELLA__', meaning: 'The second female subject' },
  { token: '__CLARA__', meaning: 'The third female subject' },
  { token: '__DIANNA__', meaning: 'The fourth female subject' },
  { token: '__EMMA__', meaning: 'The fifth female subject' },
  { token: '__ADAM__', meaning: 'Replaced with a random description of the first male subject' },
  { token: '__BOB__', meaning: 'The second male subject' },
  { token: '__HAIR__', meaning: "The hair of the character it belongs to (her / his, else the nearest name before it)" },
  { token: '__BREASTS__', meaning: 'The breasts of the female character it belongs to' },
  { token: '__VAGINA__', meaning: 'The vagina of the female character it belongs to' },
  { token: '__PENIS__', meaning: 'The penis of the male character it belongs to' },
  { token: '__ANYTHING__', meaning: 'A random line from anything.txt (one value for the whole caption, picked by the seed); the plain word if there is no such list' },
]

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

// ---- the run counts: CAPTIONS x PROMPTS x IMAGES ---------------------------------------------------
// Session-local like the seed policy (never form_state, never localStorage), so
// loading an image or reopening the dialog can never queue a large run.
const shownCaptions = computed(() => effectiveBatchSize(form.mode, form.captionCount, maxBatchSize.value))
const promptCount = computed(() => effectivePromptCount(form, MAX_VARIANTS))
const imagesIgnored = computed(() => !isCountEffective(form.seedPolicy, form.imageCount))
const shownImages = computed(() => effectiveCount(form.seedPolicy, form.imageCount, maxCount.value))
// What a Generate sends besides the form: limits, the writer and the drafts.
const batchContext = computed<BatchContext>(() => ({
  hasNegative: hasNegative.value,
  maxBatchSize: maxBatchSize.value,
  maxCount: maxCount.value,
  writer: writer.value,
  drafts: store.drafts,
}))
// A typed Manual prompt renders as written, once: PROMPTS shows 1 and is locked.
const promptsFixed = computed(() => promptsLocked(form, batchContext.value))
const shownPrompts = computed(() => (promptsFixed.value ? 1 : promptCount.value))
const plannedPrompts = computed(() => plannedSteps(form, batchContext.value, MAX_VARIANTS))
const planned = computed(() => plannedPrompts.value * shownImages.value)
const generateLabel = computed(() => (planned.value > 1 ? `Generate ×${planned.value}` : 'Generate'))
// Grades of prompts that share a caption's seeds train the writer (variant
// preference pairs), so a run that gives some caption 2+ prompts says so.
const trainsWriter = computed(() => plannedPrompts.value > shownCaptions.value)

// A committed number settles on what will really be used (whole, within the limits).
function onCaptionCount(e: Event) {
  const input = e.target as HTMLInputElement
  form.captionCount = effectiveBatchSize('random', Number(input.value), maxBatchSize.value)
  input.value = String(form.captionCount)
}
function onPromptCount(e: Event) {
  const input = e.target as HTMLInputElement
  form.promptCount = effectivePromptCount({ ...form, promptCount: Number(input.value) }, MAX_VARIANTS)
  input.value = String(form.promptCount)
}
function onImageCount(e: Event) {
  const input = e.target as HTMLInputElement
  form.imageCount = effectiveCount('increment', Number(input.value), maxCount.value)
  input.value = String(form.imageCount)
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
  const more = shownCaptions.value > 1
  const prompts = promptCount.value > 1 ? `${promptCount.value} prompts` : 'a prompt'
  switch (firstBatch.value) {
    case 'prompt':
      if ((usablePrompts(form, batchContext.value)?.length ?? 0) > 1) {
        return more
          ? `Caption 1 renders the prompts above; each caption after it is rolled and gets ${prompts} written`
          : 'Renders the prompts above'
      }
      return more
        ? `Caption 1 renders the prompt above, once; each caption after it is rolled and gets ${prompts} written`
        : 'Renders the prompt above, once'
    case 'caption':
      return more
        ? `Caption 1 is the one above; every caption gets ${prompts} written, the others are rolled`
        : `Writes ${prompts} for the caption above, then renders`
    default:
      return more
        ? `Every caption is rolled and gets ${prompts} written`
        : `Rolls a caption, writes ${prompts} for it, then renders`
  }
})

const blocker = computed(() =>
  viewingJob.value ? 'Showing a queued image: go back to the form to generate' : generateBlocker(form, {
    ready: ready.value,
    csvAvailable: csvAvailable.value,
    randomRunning: randomLocked.value,
    submitting: submitting.value,
  }),
)

function startErrorText(e: unknown): string {
  if (isGpuBusyTraining(e)) return GPU_BUSY_TEXT
  if (e instanceof ApiError && e.status === 409) {
    return 'A Random batch is already running. Cancel it or wait for it to finish.'
  }
  if (isUnknownCategoryError(e)) {
    void refreshCategories()
    return 'A chosen category no longer exists. It was removed from Categories; check them and Generate again.'
  }
  return errMsg(e)
}

async function onGenerate() {
  if (blocker.value !== null) return
  if (writesPromptsFirst(form)) {
    // Manual with only a caption: PROMPTS prompts are written into the boxes
    // first (as Generate Prompt would), and the run renders those drafts.
    submitting.value = true
    let written = false
    try {
      written = await onGeneratePrompt()
    } finally {
      submitting.value = false
    }
    if (!written || blocker.value !== null || writesPromptsFirst(form)) return
  }
  // The server owns the run: one snapshot of the form goes out, and nothing
  // typed afterwards can reach a batch that is already running.
  const req = buildBatchRequest(form, batchContext.value, MAX_VARIANTS)
  req.directions = useDirections.value
  req.category_ids = [...categoryIds.value]
  const signature = req.mode === 'manual' ? requestSignature(form, hasNegative.value, req.prompts ?? null) : null
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
  supplyingFirstStep.value = req.mode === 'random' && (!!req.prompt || !!req.prompts || !!req.caption)
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
    promptDirection.value = null
    // The seed box then holds the next unused seed, for both modes (unless the
    // user already changed it). A Random run shows the seed of the image being
    // rendered meanwhile (shownSeed), and this value is what is there when the
    // boxes unlock, whether or not the dialog was open to see the run end.
    if (r.next_seed !== null && form.seed === req.seed) form.seed = r.next_seed
    cleanForm = snapshotForm()
    toast.show(
      r.total_images === 1 ? 'Image job queued' : `${r.total_images} image jobs queued`,
      'success',
    )
  } catch (e) {
    supplyingFirstStep.value = false
    if (handledAdapterError(e)) return
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
// Order is untouched; scores are looked up per tile. `taste.scores` is a
// shallowRef replaced wholesale on scores_updated, so reading it here
// makes the list recompute on every rescore.
const gridItems = computed(() => {
  const scores = taste.scores
  const multi = multiVariantGroups(store.images)
  return store.shownImages.map((img) => {
    const item = toThumbItem(img, multi)
    return { ...item, score: scores.get(item.file_path) ?? null }
  })
})

// Clicking an image loads its saved form_state into the form and starts
// autosaving into it. Seed policy and CAPTIONS / PROMPTS / IMAGES stay as they
// are, and nothing is started (a stored Random mode only restores the mode
// and its filter). The grid only reports the FIRST click of a double-click,
// so opening the viewer never selects twice.

function onSelectTile(item: ThumbItem) {
  pending.clear() // a tile click goes back to the form
  const id = Number(item.id)
  if (!ready.value || id === store.selectedId) return
  if (randomLocked.value) {
    toast.show('A Random batch is running: wait for it to finish before loading an image.', 'info')
    return
  }
  if (item.blind) {
    // Loading it would show its prompt (and who wrote it): judge it first.
    toast.show(
      `${SCORECARD_BLIND_TEXT}: double-click to open it (Details shows its brief), then grade it ✓ or ✗ (P or X) before loading it.`,
      'info',
    )
    return
  }
  const held = store.images.find((i) => i.id === id)
  if (held?.scorecard_blind) {
    // Graded, but the row on hand is still the copy the server stripped:
    // its prompt is on its way (a click asks again if that fetch failed).
    store.fillIfRevealed(held)
    toast.show("This image's prompt is still loading: try again in a moment.", 'info')
    return
  }
  if (
    !confirmLeave(
      { selected: store.selectedId !== null, saveFailed: saveFailed.value, baseline: cleanForm, current: snapshotForm() },
      'image',
    )
  ) {
    return
  }
  autosave.flush() // pending edits belong to the image being left
  const stored = store.selectImage(id)
  if (!stored) return
  Object.assign(form, mergeFormState(form, stored))
  // Re-rendering a writer's prompt keeps its lineage (writer, model, draft):
  // the tile's draft rides along as a one-entry set, never as a typed prompt.
  const image = store.images.find((i) => i.id === id)
  store.setDrafts(image ? draftSetFromImage(image, form, servableAdapterIds(store.adapters, 't2i')) : null)
  // The form now equals what the server holds, so loading must not echo back
  // as a save. An untouched Generate would render the same image again.
  autosave.markLoaded()
  cleanForm = snapshotForm()
  lastSubmitted = requestSignature(form, hasNegative.value, usablePrompts(form, batchContext.value))
}

// The form keeps its values and goes back to being a scratch area.
function stopEditing() {
  autosave.flush()
  store.clearSelection()
  cleanForm = snapshotForm()
}

// ★ only: a favorite is not a rating, so no score goes with it.
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

// The Details panel's "delete this scorecard's graded images": the viewer
// closes afterwards, since the list it navigates holds the deleted files.
async function onDeleteScorecard(scorecardId: string): Promise<void> {
  const ok = window.confirm(
    "Delete every graded image of this scorecard? Their grades keep counting for the scorecard and for training. Images you have not graded stay.",
  )
  if (!ok) return
  try {
    const r = await store.deleteGradedScorecardImages(scorecardId)
    viewerIndex.value = null
    const parts = [`Deleted ${r.deleted} graded image${r.deleted === 1 ? '' : 's'}`]
    if (r.kept_ungraded) parts.push(`${r.kept_ungraded} ungraded kept`)
    if (r.failed) parts.push(`${r.failed} could not be moved to the trash`)
    toast.show(parts.join(' · '), r.failed ? 'warn' : 'info', 6000)
  } catch (e) {
    toast.show(errMsg(e), 'warn')
  }
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
    if (id === null) {
      void saveDraftSoon()
      // Its edits were autosaved into it, so the form is clean: a vanished
      // image must not leave a false "unsaved changes" prompt behind.
      cleanForm = snapshotForm()
    }
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
            <!-- A selected placeholder tile: the boxes show what its image was
                 submitted with, read-only; the form behind them is untouched. -->
            <div v-if="viewingJob" class="bar" :class="{ failed: pending.error.value }" role="status">
              <span>{{ jobViewText }}</span>
              <button
                type="button"
                class="link-btn"
                title="Show the form again, as it was before this job was selected"
                @click="pending.clear()"
              >Back to the form</button>
            </div>
            <div v-else-if="store.selectedImage" class="bar" :class="{ failed: saveFailed }" role="status">
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
                <span ref="tokenHelpAnchor" class="title-help">
                  <label class="sec-title" for="t2i-caption">Caption</label>
                  <button
                    type="button"
                    class="help-btn"
                    title="Which tokens are filled in"
                    aria-label="Caption tokens help"
                    :aria-expanded="tokenHelpOpen"
                    @click="tokenHelpOpen = !tokenHelpOpen"
                  >?</button>
                  <div
                    v-if="tokenHelpOpen"
                    class="token-help"
                    role="dialog"
                    aria-label="Caption tokens"
                    @keydown.esc.stop="tokenHelpOpen = false"
                  >
                    <header class="token-help-head">
                      <strong>Caption tokens</strong>
                      <button type="button" class="icon-btn small" title="Close" aria-label="Close tokens help" @click="tokenHelpOpen = false">✕</button>
                    </header>
                    <p class="token-help-note">
                      A token is a name in capitals between double underscores. Each is expanded before the prompt is written;
                      the same caption always gets the same characters. Open <em>Resolved caption</em> to see the result.
                    </p>
                    <dl class="token-help-list">
                      <template v-for="t in TOKEN_HELP" :key="t.token">
                        <dt><code>{{ t.token }}</code></dt>
                        <dd>{{ t.meaning }}</dd>
                      </template>
                    </dl>
                    <p class="token-help-note">
                      A character's first mention gets the full description; later mentions get a short handle
                      (“the auburn-haired woman”). Looks are drawn from <code>person_appearance.yaml</code>; names and lists live beside it in <code>data/t2i_captions/</code>.
                    </p>
                  </div>
                </span>
                <div class="source">
                  <div class="seg" role="radiogroup" aria-label="Caption source">
                    <label class="seg-opt">
                      <input v-model="form.mode" type="radio" name="t2i-mode" value="manual" :disabled="locked" />
                      <span>Manual</span>
                    </label>
                    <label class="seg-opt" :title="csvAvailable ? '' : (store.config?.csv.error ?? 'The caption file is not available')">
                      <input
                        v-model="form.mode"
                        type="radio"
                        name="t2i-mode"
                        value="random"
                        :disabled="locked || !csvAvailable"
                      />
                      <span>Random Caption</span>
                    </label>
                  </div>
                  <button
                    type="button"
                    class="icon-btn dice"
                    title="Load a random caption from the caption file (this empties the prompt, so Generate writes one for it)"
                    aria-label="Roll a random caption"
                    :disabled="rolling || locked || !csvAvailable"
                    @click="onRoll"
                  >🎲</button>
                  <span ref="filterAnchor" class="filter-anchor">
                    <button
                      type="button"
                      class="btn"
                      :aria-expanded="filterOpen"
                      :disabled="!csvAvailable || viewingJob"
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
              <TextEditPopup
                class="area-pop"
                title="Caption"
                width="760px"
                :rows="14"
                multiline
                :disabled="locked"
                :value="shownCaption"
                @save="onCaptionInput"
              >
                <TokenTextarea
                  id="t2i-caption"
                  class="area-host"
                  :rows="3"
                  :tokens="captionTokens"
                  :value="shownCaption"
                  :readonly="locked"
                  :placeholder="randomLocked ? 'A caption is drawn from the caption file for each caption of the run…' : 'Describe the image. Type __ for character tokens like __ALICE__ and __HAIR__: they are filled in for you, and two or more characters keep their traits apart.'"
                  @input="onCaptionInput"
                />
              </TextEditPopup>
              <div class="sec-foot">
                <button
                  type="button"
                  class="link-btn fold"
                  :aria-expanded="resolvedOpen"
                  :disabled="locked"
                  @click="toggleResolved"
                >{{ resolvedOpen ? '▾' : '▸' }} Resolved caption</button>
                <div class="writer-row">
                  <label class="inline-pick" title="Who writes the prompts: base Qwen, base Qwen shown your passed (✓) prompts as examples, or one of your trained writers (name · date · pass rate)">
                    <span>Writer</span>
                    <select
                      :value="writer"
                      :disabled="locked"
                      @change="setWriter(($event.target as HTMLSelectElement).value)"
                    >
                      <option v-for="w in builtinOptions" :key="w.id" :value="w.id">{{ w.label }}</option>
                      <option
                        v-for="a in adapterOptions"
                        :key="a.id"
                        :value="a.id"
                        :disabled="a.disabled"
                        :title="a.title"
                      >{{ a.label }}</option>
                    </select>
                  </label>
                  <button
                    type="button"
                    class="btn"
                    :disabled="generating || submitting || locked || !form.caption.trim() || !form.model"
                    :title="promptCount > 1 ? `Writes ${promptCount} prompts (PROMPTS) for the caption, to read and edit before rendering` : 'Writes the prompt for the caption, to read and edit before rendering'"
                    @click="onGeneratePrompt"
                  >{{ generating ? (promptCount > 1 ? 'Writing prompts…' : 'Writing prompt…') : (promptCount > 1 ? 'Generate Prompts' : 'Generate Prompt') }}</button>
                </div>
              </div>
              <div v-if="resolvedOpen && !locked" class="resolved">
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
              <textarea
                v-if="job?.resolved_caption"
                class="area resolved-text"
                rows="4"
                readonly
                spellcheck="false"
                aria-label="Resolved caption"
                title="The caption as the writer saw it"
                :value="job.resolved_caption"
              />
            </section>

            <section class="sec">
              <div class="sec-head">
                <div class="prompt-title">
                  <label class="sec-title" for="t2i-prompt">Prompt</label>
                  <span v-if="draftSet" class="variant-switch">
                    <button
                      type="button"
                      class="icon-btn small"
                      title="Previous variant (your edits to this one are kept)"
                      aria-label="Previous prompt variant"
                      :disabled="draftSet.current === 0"
                      @click="switchVariant(-1)"
                    >◀</button>
                    <span class="variant-label">{{ draftLabel }}</span>
                    <button
                      type="button"
                      class="icon-btn small"
                      title="Next variant (your edits to this one are kept)"
                      aria-label="Next prompt variant"
                      :disabled="draftSet.current >= draftSet.entries.length - 1"
                      @click="switchVariant(1)"
                    >▶</button>
                  </span>
                </div>
                <label class="check" title="Use the caption's classification to add a direction when the prompt is written">
                  <input
                    type="checkbox"
                    :checked="useDirections"
                    :disabled="locked"
                    @change="setDirections(($event.target as HTMLInputElement).checked)"
                  />
                  Caption directions
                </label>
              </div>
              <TextEditPopup
                class="area-pop"
                title="Prompt"
                width="900px"
                :rows="22"
                multiline
                :disabled="locked"
                :value="shownPrompt"
                @save="form.prompt = $event"
              >
                <textarea
                  id="t2i-prompt"
                  class="area area-host"
                  rows="6"
                  spellcheck="false"
                  :value="shownPrompt"
                  :readonly="locked"
                  :placeholder="randomLocked ? 'The prompts are written for each caption…' : 'The text the image model receives. Write it here, or press Generate Prompt to have it written from the caption.'"
                  @input="form.prompt = ($event.target as HTMLTextAreaElement).value"
                />
              </TextEditPopup>
              <ul v-if="!viewingJob && (promptWarnings.length || stepWarnings.length)" class="lint">
                <li v-for="w in [...promptWarnings, ...stepWarnings]" :key="w">⚠ {{ w }}</li>
              </ul>
              <p v-if="shownDirection" class="direction" :title="shownDirection.text">
                Direction · {{ shownDirection.parts.join(' · ') }}: {{ shownDirection.text }}
              </p>
              <template v-if="viewingJob ? !!job?.negative : hasNegative">
                <div>
                  <button
                    type="button"
                    class="link-btn fold"
                    :aria-expanded="negativeOpen"
                    @click="negativeOpen = !negativeOpen"
                  >{{ negativeOpen ? '▾' : '▸' }} Negative prompt{{ shownNegative.trim() ? ' •' : '' }}</button>
                </div>
                <TextEditPopup
                  v-if="negativeOpen"
                  class="area-pop"
                  title="Negative prompt"
                  width="760px"
                  :rows="12"
                  multiline
                  :disabled="locked"
                  :value="shownNegative"
                  @save="form.negative = $event"
                >
                  <textarea
                    class="area area-host"
                    rows="3"
                    spellcheck="false"
                    aria-label="Negative prompt"
                    :value="shownNegative"
                    :readonly="locked"
                    @input="form.negative = ($event.target as HTMLTextAreaElement).value"
                  />
                </TextEditPopup>
              </template>
            </section>

            <!-- Three rows on a four-column grid: Model | Workflow, then
                 Size | Aspect Ratio | Seed, then Categories. -->
            <section class="sec settings">
              <label class="fld f-model">
                <span>Model</span>
                <select v-if="viewingJob" disabled><option>{{ jobModel }}</option></select>
                <select v-else :value="form.model" @change="onModelChange(($event.target as HTMLSelectElement).value)">
                  <option v-for="m in modelOptions" :key="m.id" :value="m.id">{{ m.label }}</option>
                </select>
              </label>
              <label class="fld f-workflow">
                <span>Workflow</span>
                <select v-if="viewingJob" disabled><option>{{ jobWorkflow }}</option></select>
                <select v-else v-model="form.presetId">
                  <option :value="null" disabled>Choose a workflow…</option>
                  <option v-if="presetMissing" :value="form.presetId" disabled>Workflow #{{ form.presetId }} (not found)</option>
                  <option v-for="p in presets" :key="p.id" :value="p.id">{{ p.name }}</option>
                </select>
                <small v-if="ready && !presets.length" class="hint">none registered: add one under Configuration</small>
              </label>
              <label class="fld f-size">
                <span>Size</span>
                <select v-if="viewingJob" disabled><option>{{ jobSize }}</option></select>
                <select v-else v-model="form.megapixels">
                  <option v-for="m in sizeOptions" :key="m" :value="m">{{ m }} MP</option>
                </select>
              </label>
              <label class="fld f-aspect">
                <span>Aspect Ratio</span>
                <select
                  :value="shownAspect"
                  :disabled="locked"
                  :title="form.mode === 'random' ? 'A Random batch takes the aspect ratio of each caption' : ''"
                  @change="form.aspect = ($event.target as HTMLSelectElement).value"
                >
                  <option v-for="a in aspectOptions" :key="a" :value="a">{{ a }}</option>
                </select>
                <small v-if="form.mode === 'random' && !randomLocked" class="hint">{{ firstBatch !== 'draw' ? 'used for caption 1, then taken from each caption' : 'taken from each caption' }}</small>
              </label>
              <SeedControls
                class="f-seed"
                :seed="shownSeed"
                :policy="form.seedPolicy"
                :disabled="randomLocked"
                :readonly="viewingJob"
                :max="seedMax"
                @update:seed="form.seed = $event"
                @update:policy="form.seedPolicy = $event"
              />
              <CategorySelect v-model="categoryIds" class="f-categories" :disabled="viewingJob" />
            </section>

            <section class="sec">
              <LoraListEditor
                label="LoRAs"
                :entries="viewingJob ? (job?.loras ?? []) : form.loras"
                :readonly="viewingJob"
                @change="form.loras = $event"
              />
            </section>
          </fieldset>

          <footer class="t2i-run">
            <div class="run-row">
              <div class="counts">
                <label
                  class="fld fld-inline"
                  :class="{ 'fld-off': form.mode === 'manual' }"
                  :title="form.mode === 'manual' ? 'Manual renders the caption above: switch to Random Caption to roll more' : 'How many captions a run rolls (caption 1 is the boxes above when they hold one)'"
                >
                  <span>Captions</span>
                  <input
                    type="number"
                    min="1"
                    :max="maxBatchSize"
                    step="1"
                    :value="shownCaptions"
                    :disabled="form.mode === 'manual' || locked"
                    @change="onCaptionCount"
                  />
                </label>
                <span class="times" aria-hidden="true">×</span>
                <label
                  class="fld fld-inline"
                  :class="{ 'fld-off': promptsFixed }"
                  :title="promptsFixed ? 'A typed prompt renders as written, once. Empty the Prompt box (or press Generate Prompt) to have several written for the caption.' : 'How many prompts are written for each caption. They render the caption\'s same seeds, so they differ by prompt alone.'"
                >
                  <span>Prompts</span>
                  <input
                    type="number"
                    min="1"
                    :max="MAX_VARIANTS"
                    step="1"
                    :value="shownPrompts"
                    :disabled="promptsFixed || locked"
                    @change="onPromptCount"
                  />
                </label>
                <span class="times" aria-hidden="true">×</span>
                <label
                  class="fld fld-inline"
                  :class="{ 'fld-off': imagesIgnored }"
                  :title="imagesIgnored ? 'Ignored while the seed is Fixed: the same seed would render the same image again' : 'How many images (one seed each) to render for each prompt'"
                >
                  <span>Images</span>
                  <input
                    type="number"
                    min="1"
                    :max="maxCount"
                    step="1"
                    :value="form.imageCount"
                    :disabled="locked"
                    @change="onImageCount"
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
                  {{ generating && submitting ? 'Writing prompts…' : submitting ? 'Submitting…' : generateLabel }}
                </button>
              </div>
            </div>
            <!-- Left: what is running. Right, under Generate: what it will do, or why it can't. -->
            <div class="run-info">
              <p class="status" role="status" aria-live="polite">{{ store.statusText }}</p>
              <p v-if="blocker !== null && blocker !== 'Still loading'" class="hint">{{ blocker }}</p>
              <p v-else-if="form.mode === 'random' && !randomLocked" class="hint">{{ firstBatchHint }}</p>
              <p v-else-if="form.mode === 'manual' && firstBatch === 'caption'" class="hint">{{ firstBatchHint }}</p>
              <p v-if="trainsWriter && !randomLocked" class="hint" title="Prompts of one caption render the same seeds; grading their images (✓ / ✗) teaches the prompt writer which one worked">Grading these trains the writer</p>
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
                :selected="pending.selectedJobId.value === jobId"
                @select="pending.toggle(jobId)"
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
      :allow-rating="true"
      :favorite-mirror="store.applyFavoriteFlag"
      :judgment-mirror="store.applyJudgment"
      @close="viewerIndex = null"
    >
      <!-- What made the image on show: the store's live row, so a grade
           that lifts a scorecard blind fills the prompt in. -->
      <template #details="{ media, blind }">
        <T2iImageInfo
          :image="store.imageAt(media.file_path)"
          :blind="blind"
          :presets="presets"
          :adapters="store.adaptersLoaded ? store.adapters : null"
          :models="models"
          @delete-scorecard="onDeleteScorecard"
        />
      </template>
    </MediaViewer>
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

.title-help {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.help-btn {
  width: 16px;
  height: 16px;
  padding: 0;
  border: 1px solid var(--surface-border);
  border-radius: 50%;
  background: none;
  color: var(--text-color-secondary);
  font-size: 10px;
  font-weight: 700;
  line-height: 14px;
  cursor: pointer;
}

.help-btn:hover,
.help-btn[aria-expanded='true'] {
  color: var(--text-color);
  border-color: var(--text-color-secondary);
}

.token-help {
  position: absolute;
  top: calc(100% + 6px);
  left: 0;
  z-index: 20;
  width: min(440px, 80vw);
  padding: 10px 12px;
  border: 1px solid var(--surface-border);
  border-radius: 8px;
  background: var(--surface-overlay, var(--surface-card));
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.3);
  font-size: 12px;
  color: var(--text-color);
}

.token-help-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
}

.token-help-note {
  margin: 0 0 8px;
  color: var(--text-color-secondary);
  line-height: 1.4;
}

.token-help-note:last-child {
  margin: 8px 0 0;
}

.token-help-list {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 4px 10px;
  margin: 0;
}

.token-help-list dt {
  white-space: nowrap;
}

.token-help-list dd {
  margin: 0;
  line-height: 1.35;
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

/* The Writer sits beside Generate Prompt, the button it shapes. */
.writer-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 6px 10px;
}

.inline-pick {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text-color-secondary);
}

.inline-pick select {
  font-family: inherit;
  font-size: 12px;
  color: var(--text-color);
  background: var(--surface-ground);
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  padding: 4px 6px;
}

.inline-pick select:focus {
  outline: none;
  border-color: var(--primary-color);
}

.inline-pick select:disabled {
  opacity: 0.6;
}

.prompt-title {
  display: inline-flex;
  align-items: center;
  gap: 10px;
}

.variant-switch {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  font-size: 12px;
  color: var(--text-color-secondary);
}

.icon-btn.small {
  font-size: 11px;
  padding: 2px 4px;
}

.variant-label {
  white-space: nowrap;
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

.f-categories {
  grid-column: 1 / -1;
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
  align-items: center;
  gap: 8px 10px;
}

.counts .times {
  color: var(--text-color-secondary);
  font-size: 12px;
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

.check {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  cursor: pointer;
}

.direction {
  margin: 0;
  font-size: 12px;
  opacity: 0.8;
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

/* The form's wrapper takes no box of its own: its sticky rows stay children of
   the scrolling column as far as layout is concerned. */

/* TextEditPopup centres its button against an input; beside a box it sits at
   the top and the box fills the row. */
.area-pop { align-items: flex-start; width: 100%; }
.area-pop .area-host { flex: 1; min-width: 0; }
</style>
