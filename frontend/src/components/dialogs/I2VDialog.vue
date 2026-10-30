<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { Media } from '../../types/media'
import {
  cadenceChips,
  formatI2vTimestamp,
  i2vDims,
  withCurrentOption,
  type I2vFix,
  I2V_WRITERS,
  type I2vFormState,
  type I2vWriter,
  type I2vTemplate,
  type I2vVideo,
} from '../../types/i2v'
import { useI2vStore } from '../../stores/i2v'
import { generatePrompt, generateVideo, deleteI2vVideo, lintI2vPrompt } from '../../api/i2v'
import { thumbnailUrl } from '../../api/client'
import {
  ADAPTER_LOADING,
  ADAPTER_LOADING_TEXT,
  ADAPTER_UNAVAILABLE,
  adapterErrorCode,
  adapterErrorWriter,
  adapterMenuOptions,
  isAdapterWriterId,
} from '../../types/writers'
import { useWebSocket } from '../../composables/useWebSocket'
import { useWriterPick } from '../../composables/useWriterPick'
import { useTrainingStore } from '../../stores/training'
import { useToast } from '../../composables/useToast'
import { errorText } from '../../utils/gpuBusy'
import { isUnknownCategoryError, useGenerationCategories } from '../../composables/useGenerationCategories'
import { useMediaStore } from '../../stores/media'
import { useFoldersStore } from '../../stores/folders'
import MediaViewer from '../viewer/MediaViewer.vue'
import I2vClipList from '../generation/I2vClipList.vue'
import I2vPromptEditor from '../generation/I2vPromptEditor.vue'
import JobTile from '../generation/JobTile.vue'
import SeedControls from '../generation/SeedControls.vue'
import CategorySelect from '../generation/CategorySelect.vue'
import LoraListEditor from '../storyboard/LoraListEditor.vue'
import { SEED_MAX, randomSeed, type SeedPolicy } from '../../types/t2i'
import type { LoraEntry } from '../../types/storyboard'

const props = defineProps<{ media: Media }>()
const emit = defineEmits<{ close: [] }>()

const store = useI2vStore()
const mediaStore = useMediaStore()
const foldersStore = useFoldersStore()
const toast = useToast()
const training = useTrainingStore()

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

// Découpage cadence. '' = Single take (today's single-shot path). A
// template fixes the clip duration, so selecting one sets durationS and
// locks the Duration select; Single take unlocks it and keeps the value.
const templateId = ref<string>('')
const selectedTemplate = computed(
  () => store.templates.find((t) => t.id === templateId.value) ?? null,
)
const durationLocked = computed(() => selectedTemplate.value !== null)
const cadence = computed(() => (selectedTemplate.value ? cadenceChips(selectedTemplate.value) : []))
// Available cadences first (alphabetical), then unavailable ones
// (alphabetical) -- so a duration-config change that strands a template
// pushes it to the bottom instead of scattering it mid-list.
const orderedTemplates = computed(() => {
  const byName = (a: I2vTemplate, b: I2vTemplate) => a.name.localeCompare(b.name)
  const available = store.templates.filter((t) => t.available).sort(byName)
  const unavailable = store.templates.filter((t) => !t.available).sort(byName)
  return [...available, ...unavailable]
})
// Loading a clip whose template file has since been removed.
const missingTemplateNote = ref('')

// Re-asserts the template's current duration_s whenever the selected
// template changes, including on load (loadClip assigns templateId
// directly rather than going through onCadenceChange below). If a
// template's duration_s is edited in the library after clips were made
// with it, reloading one of those clips drifts the form to the template's
// new duration rather than the one the clip actually rendered at.
watch(selectedTemplate, (t) => {
  if (t) durationS.value = t.duration_s
})

// The select's own @change handler (not v-model) so the note is cleared
// exactly on a genuine user pick and never by loadClip's own assignment.
// A watch(templateId) alternative was considered, but loadClip sets
// templateId then the note synchronously, and the watcher fires later
// (post-flush) -- an unconditional clear there would wipe the note
// loadClip just set; a suppress-flag guard is also unsound because
// assigning templateId to its *current* value (e.g. loading a second
// missing-template clip while already on Single take) doesn't trigger the
// watcher at all, leaving the flag stuck and swallowing the next real
// user change. Tying the clear directly to the change event has neither
// problem.
function onCadenceChange(id: string) {
  templateId.value = id
  missingTemplateNote.value = ''
}

const expanding = ref(false)

// Writer: per browser like the seed policy, never per clip (form_state does
// not carry it), so loading a clip never changes it. See the Writer pick below.
const WRITER_STORAGE_KEY = 'metascan.i2v.writer.v1'

// The held draft stops describing the prompt when the idea or cadence it was
// written for changes (the source image and a selected clip clear it in the
// store / loadClip). Typing in the Prompt box does not: that is an edit.
watch([idea, templateId], () => store.setDraft(null))
const submitting = ref(false)
// Which clip list the viewer was opened from, and the clip's place in it.
const viewerIndex = ref<{ list: 'left' | 'right'; index: number } | null>(null)
const promptEditor = ref<InstanceType<typeof I2vPromptEditor> | null>(null)
const loadingMoreAll = ref(false)

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
  flushFormState() // before store.close() empties the clip list
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

// withCurrentOption: a loaded clip may carry a value that has since left
// the config lists; it must still be shown, not blanked.
const durations = computed(() =>
  withCurrentOption(store.config?.durations ?? [6, 10, 15, 20], durationS.value),
)
const megapixelOptions = computed(() =>
  withCurrentOption(store.config?.megapixels ?? [0.25, 0.5, 0.75, 1.0], megapixels.value),
)
const stepOptions = computed(() =>
  withCurrentOption(store.config?.steps ?? [20, 25, 30, 35, 40], steps.value),
)
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

// ---- seed policy + batch generate -------------------------------------------
// The seed policy advances the seed once per submitted job, so the
// field ends up showing the next unused seed. Fixed is the default and is
// exactly the old behaviour: one job, seed untouched.
const MAX_GENERATE_COUNT = 20

const seedPolicy = ref<SeedPolicy>('fixed')
const generateCount = ref(1)

// Categories every clip of the next Generate is tagged with. Session-local
// like the seed policy: not part of form_state, so loading a clip never
// changes it.
const { selected: categoryIds, refresh: refreshCategories } = useGenerationCategories(
  'metascan.i2v.categories.v1',
)

// Prompt-writer adapters after Base: the I2V ones scoped to `all` or to a
// selected category, labelled with their scorecard pass rate. (Training
// creates T2I adapters only, so this list is empty today; the scorecard
// compares T2I writers, so I2V's Base carries no baseline rate.)
const adapterOptions = computed(() =>
  adapterMenuOptions(store.adapters, {
    kind: 'i2v',
    categoryIds: categoryIds.value,
    targetModel: null,
    scorecards: training.scorecards,
  }),
)
useWebSocket('training', (event) => {
  if (event === 'adapters_changed') {
    writerPick.clearRefusal()
    void store.loadAdapters()
  }
})
// The Writer pick (see useWriterPick): the user's choice survives a transient
// fallback to Base and comes back when its adapter is offered again; only a
// deleted adapter drops it. Never part of a clip's form.
const writerPick = useWriterPick<I2vWriter>({
  storageKey: WRITER_STORAGE_KEY,
  builtinIds: I2V_WRITERS.map((w) => w.id),
  adapters: () => store.adapters,
  adaptersLoaded: () => store.adaptersLoaded,
  options: () => adapterOptions.value,
})
const writer = writerPick.writer
const setWriter = writerPick.setWriter
// True when `e` was an adapter refusal, after dealing with it (see T2IDialog).
function handledAdapterError(e: unknown): boolean {
  const code = adapterErrorCode(e)
  if (code === ADAPTER_LOADING) {
    toast.show(ADAPTER_LOADING_TEXT, 'info', 6000)
    return true
  }
  if (code === ADAPTER_UNAVAILABLE) {
    const refused = adapterErrorWriter(e) ?? writer.value
    if (refused === writer.value && isAdapterWriterId(refused)) {
      writerPick.refuse(refused)
    } else if (store.draft?.writer_id === refused) {
      store.setDraft(null) // the prompt goes out typed next time
    }
    void store.loadAdapters()
    return true
  }
  return false
}
const batchProgress = ref('')

// A Fixed seed would render the same video N times, so the count is ignored
// rather than obeyed -- the field keeps whatever was typed for when the
// policy changes back.
const countIgnored = computed(() => seedPolicy.value === 'fixed')
const effectiveCount = computed(() => {
  if (countIgnored.value) return 1
  const n = Math.round(Number(generateCount.value))
  if (!Number.isFinite(n)) return 1
  return Math.min(MAX_GENERATE_COUNT, Math.max(1, n))
})

// null = the next seed would leave the 0..2^31-1 range, so the batch stops
// instead of silently repeating a seed.
function nextSeed(current: number): number | null {
  switch (seedPolicy.value) {
    case 'increment':
      return current < SEED_MAX ? current + 1 : null
    case 'decrement':
      return current > 0 ? current - 1 : null
    case 'random':
      return randomSeed()
    default:
      return current
  }
}

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
    const res = await lintI2vPrompt({
      prompt: text,
      duration_s: durationS.value,
      template_id: templateId.value || null,
    })
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

watch([prompt, durationS, templateId], () => {
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
  // What the draft is written for, read before the await: the user may keep
  // typing while the writer works.
  const forIdea = idea.value
  const forTemplate = templateId.value
  try {
    const res = await generatePrompt({
      source_path: props.media.file_path,
      idea: forIdea,
      duration_s: durationS.value,
      template_id: templateId.value || null,
      writer: writer.value,
    })
    prompt.value = res.prompt
    warnings.value = res.warnings
    store.setDraft({
      text: res.prompt,
      writer_id: res.writer_id,
      writer_model: res.writer_model ?? null,
      idea: forIdea,
      templateId: forTemplate,
    })
  } catch (e) {
    if (handledAdapterError(e)) return
    toast.show(errorText(e), 'warn')
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
    template: templateId.value || null,
  })
}

// Signature of the last SUCCESSFUL submit from this dialog instance.
// Component-local on purpose: earlier sessions/runs are not considered --
// with one deliberate exception: loading a clip (loadClip) arms it, since
// that clip's exact seed and settings are now in the form and an untouched
// Generate would re-render the very same video.
const lastSubmitted = ref<string | null>(null)

// ---- per-clip form state ----------------------------------------------------
// Nothing is saved until a clip exists: with no clip selected the form is a
// scratch area, exactly as before. Clicking a clip loads its form_state and
// from then on every change autosaves INTO THAT CLIP -- its form_state only.
// The clip's as-rendered fields (prompt_used, seed, quality, steps, which
// feed the tile label) are facts about the video and are never written.
const selectedId = ref<number | null>(null)
const selectedVideo = computed(
  () => store.videos.find((v) => v.id === selectedId.value) ?? null,
)
const saveFailed = ref(false)
// JSON of the form as last loaded or saved; an autosave is skipped while
// the form still equals it (loading a clip must not echo straight back).
let savedSnapshot = ''
let saveTimer: ReturnType<typeof setTimeout> | null = null
// Saves run one at a time, in order, so a slow early request can never
// land after -- and overwrite -- a later one.
let saveChain: Promise<void> = Promise.resolve()

function currentForm(): I2vFormState {
  return {
    idea: idea.value,
    prompt: prompt.value,
    duration_s: durationS.value,
    quality: quality.value,
    megapixels: megapixels.value,
    steps: steps.value,
    seed: seed.value,
    loras: loras.value.map((l) => ({ name: l.name, strength: l.strength })),
    template_id: templateId.value || null,
  }
}

// A number input emptied mid-edit yields '' (v-model.number), and the
// server rejects a non-number. Leave such a field out: its last good value
// stays saved, and everything else still goes through.
function savableFields(form: I2vFormState): Partial<I2vFormState> {
  const out: Partial<I2vFormState> = { ...form }
  if (!Number.isInteger(form.seed)) delete out.seed
  if (!(typeof form.steps === 'number' && form.steps > 0)) delete out.steps
  if (!(typeof form.duration_s === 'number' && form.duration_s > 0)) delete out.duration_s
  if (!(typeof form.megapixels === 'number' && form.megapixels > 0)) delete out.megapixels
  return out
}

function flushFormState() {
  if (saveTimer) {
    clearTimeout(saveTimer)
    saveTimer = null
  }
  const id = selectedId.value
  if (id == null) return
  const form = currentForm()
  const snapshot = JSON.stringify(form)
  if (snapshot === savedSnapshot) return
  // Captured now, sent later: by the time the chain reaches this save the
  // user may have clicked a different clip.
  const fields = savableFields(form)
  saveChain = saveChain.then(async () => {
    try {
      await store.saveFormState(id, fields)
      if (selectedId.value === id) savedSnapshot = snapshot
      saveFailed.value = false
    } catch {
      // Snapshot left alone, so the next edit (or close) retries.
      saveFailed.value = true
    }
  })
}

watch(
  [idea, prompt, durationS, quality, megapixels, steps, seed, loras, templateId],
  () => {
    if (selectedId.value == null) return
    if (saveTimer) clearTimeout(saveTimer)
    saveTimer = setTimeout(flushFormState, 600)
  },
  { deep: true },
)

function loadClip(v: I2vVideo) {
  flushFormState() // pending edits belong to the clip being left
  // A clip's prompt is not the writer's draft.
  store.setDraft(null)
  const f = v.form_state
  idea.value = f.idea ?? ''
  prompt.value = f.prompt ?? ''
  // Nulls only occur on clips that predate form_state (or were ingested
  // across a server restart); keep the form's current value for those.
  if (f.duration_s != null) durationS.value = f.duration_s
  if (f.quality != null) quality.value = f.quality
  if (f.megapixels != null) megapixels.value = f.megapixels
  if (f.steps != null) steps.value = f.steps
  if (f.seed != null) seed.value = f.seed
  loras.value = (f.loras ?? []).map((l) => ({ name: l.name, strength: l.strength }))
  const tid = f.template_id ?? ''
  if (tid && !store.templates.some((t) => t.id === tid)) {
    templateId.value = ''
    missingTemplateNote.value = `This clip used cadence "${tid}", which is no longer in the library; loaded as Single take.`
  } else {
    templateId.value = tid
    missingTemplateNote.value = ''
  }
  warnings.value = []
  selectedId.value = v.id
  savedSnapshot = JSON.stringify(currentForm())
  saveFailed.value = false
  lastSubmitted.value = requestSignature()
}

// The tile waits out the double-click window (useDelayedClick), so a
// double-click never reaches here.
function onSelectClip(v: I2vVideo) {
  loadClip(v)
}

// A clip of another source image cannot be loaded here yet (switching the
// source comes with the run row); one of this image's clips loads as in the
// left list.
function onSelectAllClip(v: I2vVideo) {
  const own = store.videos.find((x) => x.id === v.id)
  if (own) onSelectClip(own)
}

async function onShowMoreAll() {
  loadingMoreAll.value = true
  try {
    await store.loadOlderVideos()
  } catch (e) {
    toast.show(e instanceof Error ? e.message : String(e), 'warn')
  } finally {
    loadingMoreAll.value = false
  }
}

// The form keeps its values and goes back to being a scratch area.
function stopEditingClip() {
  flushFormState()
  selectedId.value = null
}

// The selected clip can vanish underneath us: deleted here, or pruned by
// the server because its media row is gone.
watch(
  () => store.videos,
  (videos) => {
    if (selectedId.value != null && !videos.some((v) => v.id === selectedId.value)) {
      if (saveTimer) clearTimeout(saveTimer)
      saveTimer = null
      selectedId.value = null
    }
  },
)

onBeforeUnmount(flushFormState)

async function onGenerate() {
  if (!prompt.value.trim()) {
    toast.show('Write or generate a prompt first', 'warn')
    return
  }
  const total = effectiveCount.value
  // Checked once for the whole batch: with any policy but Fixed the seed
  // moves, so every later submit differs from this one by construction.
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
  const d = store.draft
  const heldDraft =
    d && d.idea === idea.value && d.templateId === templateId.value ? d : null
  submitting.value = true
  let queued = 0
  let stopped: string | null = null
  try {
    for (let i = 0; i < total; i++) {
      batchProgress.value = total > 1 ? `${i + 1}/${total}` : ''
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
          template_id: templateId.value || null,
          category_ids: [...categoryIds.value],
          // Lineage: only a live draft for this idea and cadence counts; a
          // prompt without one is typed from scratch (recorded `manual`).
          ...(heldDraft
            ? {
                writer_id: heldDraft.writer_id,
                draft_prompt: heldDraft.text,
                writer_model: heldDraft.writer_model,
              }
            : {}),
          ...(stepsEnabled.value ? { steps: steps.value } : {}),
        })
        warnings.value = res.warnings
        // The signature of what was just submitted: the seed has not
        // advanced yet at this point.
        lastSubmitted.value = requestSignature()
        store.trackJob(res.job_id)
        queued += 1
      } catch (e) {
        if (adapterErrorCode(e) === ADAPTER_UNAVAILABLE) {
          // The draft's adapter has gone: the prompt goes out as typed next.
          store.setDraft(null)
          void store.loadAdapters()
          stopped = 'the trained writer of this prompt is no longer available; Generate again to send it as typed'
        } else if (isUnknownCategoryError(e)) {
          void refreshCategories()
          stopped =
            'A chosen category no longer exists. It was removed from Categories; check them and Generate again.'
        } else {
          stopped = errorText(e)
        }
        break
      }
      const advanced = nextSeed(seed.value)
      if (advanced === null) {
        // Leave the seed where it is rather than repeat it. Only a batch
        // with submits still owing has actually been cut short.
        if (i + 1 < total) stopped = 'the seed reached the end of its range'
        break
      }
      seed.value = advanced
    }
  } finally {
    submitting.value = false
    batchProgress.value = ''
  }
  if (stopped) {
    toast.show(total > 1 ? `${stopped} — queued ${queued} of ${total}` : stopped, 'warn')
  } else {
    toast.show(total > 1 ? `${total} video jobs queued` : 'Video job queued', 'success')
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
  if (selectedId.value === v.id) {
    // Drop any pending save rather than PATCH a row that is about to go.
    if (saveTimer) clearTimeout(saveTimer)
    saveTimer = null
    selectedId.value = null
  }
  try {
    await deleteI2vVideo(v.id)
    store.removeVideo(v.id)
    await store.refreshVideos()
    void mediaStore.loadAllMedia()
    void foldersStore.refreshI2vSources()
  } catch (e) {
    toast.show(e instanceof Error ? e.message : String(e), 'warn')
  }
}

// The tile star is ★ only (never the grade); the ✓ / ✗ badge is read-only
// and grading happens in the viewer (P / X).
async function onToggleFavorite(v: I2vVideo) {
  try {
    await store.toggleFavorite(v)
  } catch (e) {
    toast.show(e instanceof Error ? e.message : String(e), 'warn')
  }
}

// Synthesized Media list for the read-only viewer (ShotHeader precedent), built
// from the clips of the list the viewer was opened from: each clip's own
// library row by path when it is there, else the source image's row spread.
// The clip's own ★ and grade always win over either: the viewer's rail shows
// them until the clip's judgment arrives.
const pathKey = (p: string) => p.replace(/\\/g, '/')
const viewerMedia = computed<Media[]>(() => {
  if (!viewerIndex.value) return []
  const clips = viewerIndex.value.list === 'left' ? store.videos : store.allVideos
  const byPath = new Map(mediaStore.allMedia.map((m) => [pathKey(m.file_path), m]))
  return clips.map((v) => {
    const row = byPath.get(pathKey(v.file_path))
    const base: Media = row ?? {
      ...props.media,
      file_path: v.file_path,
      file_name: v.file_path.split(/[\\/]/).pop() ?? v.file_path,
      is_video: true,
    }
    return { ...base, is_favorite: v.is_favorite, grade: v.grade ?? null }
  })
})
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

      <div class="i2v-body">
        <!-- Left: the source image and its own clips, newest first. -->
        <section class="i2v-side i2v-left" aria-label="This image's videos">
          <img class="i2v-source" :src="thumbnailUrl(media.file_path)" alt="" />
          <I2vClipList
            class="side-list"
            :videos="store.videos"
            :selected-id="selectedId"
            :columns="1"
            :has-jobs="store.jobs.size > 0"
            empty-text="No videos yet — generated clips appear here."
            @select="onSelectClip"
            @play="viewerIndex = { list: 'left', index: $event }"
            @favorite="onToggleFavorite"
            @delete="onDeleteVideo"
          >
            <template #jobs>
              <JobTile
                v-for="[jobId, chip] in store.jobs"
                :key="'job-' + jobId"
                :chip="chip"
                @cancel="onCancelJob(jobId)"
                @dismiss="store.dismissJob(jobId)"
              />
            </template>
          </I2vClipList>
        </section>

        <!-- Middle: the form is ONE scroll box, so every right edge shares one
             gutter. The editing bar sticks to its top and the run row to its
             bottom. -->
        <div class="i2v-main">
          <!-- Shown only while a clip is selected: says where edits are going.
               With no clip selected the form is an unsaved scratch area. -->
          <div v-if="selectedVideo" class="i2v-top">
            <div class="editing-bar" :class="{ failed: saveFailed }">
              <span v-if="saveFailed">
                Couldn't save your changes to this clip — they'll be retried on the next edit.
              </span>
              <span v-else>
                Editing the clip from {{ formatI2vTimestamp(selectedVideo.created_at) }} ·
                changes save automatically
              </span>
              <button
                class="link-btn"
                title="Keep these values in the form, but stop saving them to this clip"
                @click="stopEditingClip"
              >Stop editing</button>
            </div>
          </div>

          <div class="cadence-row">
            <label class="fld cadence-pick">
              <span>Cadence</span>
              <select
                :value="templateId"
                @change="onCadenceChange(($event.target as HTMLSelectElement).value)"
              >
                <option value="">Single take</option>
                <option
                  v-for="t in orderedTemplates"
                  :key="t.id"
                  :value="t.id"
                  :disabled="!t.available"
                  :title="t.unavailable_reason ?? t.description"
                >
                  {{ t.name }} · {{ t.duration_s }}s{{ t.available ? '' : ' (unavailable)' }}
                </option>
              </select>
            </label>
          </div>
          <small v-if="selectedTemplate" class="dims-hint">{{ selectedTemplate.description }}</small>
          <small v-if="missingTemplateNote" class="dims-hint warn">{{ missingTemplateNote }}</small>
          <div
            v-if="cadence.length"
            class="cadence"
            title="The shot plan this cadence fixes; the prompt is written to it"
          >
            <span v-for="(c, i) in cadence" :key="i" class="cadence-chip" :class="c.kind">{{
              c.label
            }}</span>
          </div>

          <label class="fld">
            <span>Idea</span>
            <textarea
              v-model="idea"
              rows="3"
              placeholder="Short idea for the video (optional)"
            />
          </label>
          <div class="writer-row">
            <label class="inline-pick" title="Who writes the prompt: Base (the unguided prompt-writer model) or one of your trained writers (name · date · pass rate)">
              <span>Writer</span>
              <select
                :value="writer"
                @change="setWriter(($event.target as HTMLSelectElement).value)"
              >
                <option v-for="w in I2V_WRITERS" :key="w.id" :value="w.id">{{ w.label }}</option>
                <option
                  v-for="a in adapterOptions"
                  :key="a.id"
                  :value="a.id"
                  :disabled="a.disabled"
                  :title="a.title"
                >{{ a.label }}</option>
              </select>
            </label>
            <button class="btn" :disabled="expanding" @click="onExpandPrompt">
              {{ expanding ? 'Generating prompt…' : 'Generate prompt' }}
            </button>
          </div>

          <I2vPromptEditor ref="promptEditor" v-model="prompt" />
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

          <div class="params">
            <label
              class="fld"
              :class="{ 'fld-off': durationLocked }"
              :title="durationLocked ? 'Set by the cadence' : ''"
            >
              <span>Duration</span>
              <select v-model.number="durationS" :disabled="durationLocked">
                <option v-for="d in durations" :key="d" :value="d">{{ d }}s</option>
              </select>
              <small v-if="durationLocked" class="dims-hint">set by the cadence</small>
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
            <SeedControls
              class="i2v-seed"
              :seed="seed"
              :policy="seedPolicy"
              :max="SEED_MAX"
              @update:seed="seed = $event"
              @update:policy="seedPolicy = $event"
            />
          </div>
          <CategorySelect v-model="categoryIds" class="i2v-categories" />
          <LoraListEditor
            label="LoRAs"
            :entries="loras"
            @change="loras = $event"
          />

          <footer class="i2v-run">
            <div class="run-row">
              <label
                class="fld fld-inline"
                :class="{ 'fld-off': countIgnored }"
                title="How many videos one Generate queues"
              >
                <span>Generate count</span>
                <input
                  v-model.number="generateCount"
                  type="number"
                  min="1"
                  :max="MAX_GENERATE_COUNT"
                />
              </label>
              <div class="actions">
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
                  {{ submitting
                    ? (batchProgress ? `Generating ${batchProgress}…` : 'Submitting…')
                    : (effectiveCount > 1 ? `Generate ×${effectiveCount}` : 'Generate') }}
                </button>
              </div>
            </div>
            <small v-if="countIgnored" class="dims-hint">count ignored while the seed is Fixed</small>
          </footer>
        </div>

        <!-- Right: every I2V video in the library, newest first. -->
        <section class="i2v-side i2v-right" aria-labelledby="i2v-videos-title">
          <h4 id="i2v-videos-title" class="sec-title">Videos</h4>
          <I2vClipList
            class="side-list"
            :videos="store.allVideos"
            :selected-id="selectedId"
            :columns="2"
            :has-more="store.hasMoreAll"
            :loading-more="loadingMoreAll"
            empty-text="No videos yet."
            @select="onSelectAllClip"
            @play="viewerIndex = { list: 'right', index: $event }"
            @favorite="onToggleFavorite"
            @delete="onDeleteVideo"
            @more="onShowMoreAll"
          />
        </section>
      </div>
    </div>

    <MediaViewer
      v-if="viewerIndex !== null"
      :media-list="viewerMedia"
      :initial-index="viewerIndex.index"
      :allow-destructive="false"
      :allow-rating="true"
      :favorite-mirror="store.applyFavoriteFlag"
      :judgment-mirror="store.applyJudgment"
      @close="viewerIndex = null"
    />
  </div>
</template>

<style scoped>
/* The Writer pick sits beside Generate prompt, the button it shapes. */
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

/* Three panels: this image's clips, the form, every clip. The form decides the
   card's height (up to 94vh) and the side panels, height 0 stretched to the
   row, never make it taller. */
.i2v-card {
  background: var(--surface-section);
  border-radius: 12px;
  padding: 16px 22px 18px;
  width: min(1500px, 96vw);
  max-height: 94vh;
  overflow: hidden;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.i2v-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
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

.i2v-body {
  flex: 1 1 auto;
  min-height: 0;
  display: grid;
  grid-template-columns: 240px minmax(0, 1fr) 400px;
  grid-template-rows: minmax(0, 1fr);
  gap: 20px;
}

/* The gutter is reserved so nothing changes width when a fold opens and the
   column starts to scroll. */
.i2v-main {
  min-width: 0;
  min-height: 0;
  overflow-y: auto;
  scrollbar-gutter: stable;
  padding-right: 6px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.i2v-top {
  position: sticky;
  top: 0;
  z-index: 2;
  background: var(--surface-section);
}

.i2v-side {
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

.side-list { flex: 1 1 auto; }
.sec-title { margin: 0; font-size: 13px; color: var(--text-color); }
.i2v-source { width: calc(100% - 8px); height: auto; max-height: 180px; object-fit: contain; border-radius: 6px; }

.cadence-row { display: flex; justify-content: flex-end; }
.cadence-pick { flex-direction: row; align-items: center; gap: 8px; }

.i2v-run {
  position: sticky;
  bottom: 0;
  z-index: 2;
  flex: none;
  margin-top: auto;
  background: var(--surface-section);
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding-top: 12px;
  border-top: 1px solid var(--surface-border);
}
.run-row { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 10px 20px; }
.fld-inline { flex-direction: row; align-items: center; gap: 8px; }
.fld-inline input { width: 70px; }
.actions { display: flex; align-items: center; gap: 8px; }

/* Too narrow for three panels: one column, the card scrolls, nothing shrinks. */
@media (max-width: 1099px) {
  .i2v-card { overflow-y: auto; }
  .i2v-body { display: flex; flex-direction: column; flex: none; min-height: auto; }
  .i2v-main { flex: none; min-height: auto; overflow: visible; padding-right: 0; }
  .i2v-side { flex: none; height: 440px; min-height: 0; }
}

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
.i2v-categories { flex: 1 1 100%; }
.dims-hint { color: var(--text-muted, #888); font-size: 11px; margin-top: 2px; }
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
.fld-off { opacity: 0.55; }
.editing-bar {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  padding: 6px 10px; border-radius: 6px; font-size: 12px;
  color: var(--text-color-secondary);
  background: var(--surface-ground);
  border: 1px solid var(--surface-border);
  border-left: 3px solid var(--primary-color, #6366f1);
}
.editing-bar.failed { border-left-color: #c33; color: #c33; }
.link-btn {
  background: none; border: none; padding: 0; cursor: pointer; font-size: 12px;
  color: var(--primary-color, #6366f1); white-space: nowrap;
}
.link-btn:hover { text-decoration: underline; }
.cadence { display: flex; flex-wrap: wrap; gap: 6px; margin: 4px 0 8px; }
.cadence-chip {
  font-size: 11px; padding: 2px 8px; border-radius: 10px; white-space: nowrap;
  color: var(--text-color-secondary); background: var(--surface-ground);
  border: 1px solid var(--surface-border);
}
.cadence-chip.cut { border-color: var(--primary-color, #6366f1); color: var(--text-color); }
.cadence-chip.j_cut { border-style: dashed; border-color: var(--primary-color, #6366f1); color: var(--text-color); }
.dims-hint.warn { color: #c33; }
</style>
