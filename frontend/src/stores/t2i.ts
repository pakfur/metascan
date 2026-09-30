import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { useDebounceFn } from '@vueuse/core'
import * as t2iApi from '../api/t2i'
import { updateMedia } from '../api/media'
import { useJobTracker } from '../composables/useJobTracker'
import { useToast } from '../composables/useToast'
import { useFoldersStore } from './folders'
import { useMediaStore } from './media'
import {
  DRAFT_STORAGE_KEY,
  batchStatusLine,
  cleanFilter,
  mergeImagePage,
  mergeOlderPage,
  sanitizeDraft,
  type CaptionCount,
  type CaptionFilter,
  type CaptionMeta,
  type CaptionRow,
  type T2iActiveBatch,
  type T2iBatchInfo,
  type T2iBatchOutcome,
  type T2iBatchRequest,
  type T2iBatchStepEvent,
  type T2iConfig,
  type T2iFinishedBatch,
  type T2iFormState,
  type T2iImage,
  type T2iMode,
  type T2iPromptResult,
  type T2iResolveResult,
  type T2iStartResult,
} from '../types/t2i'

const IMAGE_PAGE_SIZE = 60
// New images reach the library grid through one debounced reload, not one
// per image; the max wait keeps a long, steady batch from postponing it
// until the batch is over.
const MEDIA_RELOAD_DEBOUNCE_MS = 1500
const MEDIA_RELOAD_MAX_WAIT_MS = 5000
// How many ended batch ids are remembered so a late frame cannot revive one.
const FINISHED_IDS_CAP = 200

const errorMessage = (e: unknown): string => (e instanceof Error ? e.message : String(e))
const asStr = (v: unknown, fallback = ''): string => (typeof v === 'string' ? v : fallback)
const asNum = (v: unknown, fallback: number): number =>
  typeof v === 'number' && Number.isFinite(v) ? v : fallback
const asNumOrNull = (v: unknown): number | null =>
  typeof v === 'number' && Number.isFinite(v) ? v : null
// Job and media rows are POSIX or native depending on the endpoint.
const samePath = (a: string, b: string): boolean => a.replace(/\\/g, '/') === b.replace(/\\/g, '/')
const cloneJson = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T

/**
 * State and actions behind the Text to Image dialog.
 *
 * The store never subscribes to the WebSocket: useWebSocket needs a component
 * setup context, so the dialog subscribes to `t2i` and `comfy` and forwards
 * every message to handleT2iEvent / handleComfyEvent. After a reconnect the
 * dialog should call reattach() to pick up what it missed.
 */
export const useT2iStore = defineStore('t2i', () => {
  const toast = useToast()
  const media = useMediaStore()
  const folders = useFoldersStore()
  // Job chips: any ComfyUI job that carries a t2i_batch_id.
  const tracker = useJobTracker({ match: (job) => !!job.t2i_batch_id })

  // ---- state ---------------------------------------------------------------

  const config = ref<T2iConfig | null>(null)
  const configLoading = ref(false)
  const configError = ref<string | null>(null)
  const captionMeta = ref<CaptionMeta | null>(null)
  const metaLoading = ref(false)
  const metaError = ref<string | null>(null)

  // The strip, newest first. `imagesLoaded` turns true after the first
  // successful load of a session: the strip waits for it before it may say
  // "No images yet".
  const images = ref<T2iImage[]>([])
  const hasMoreImages = ref(false)
  const imagesLoaded = ref(false)
  const imagesLoading = ref(false)
  const imagesError = ref<string | null>(null)
  const loadingOlder = ref(false)
  const selectedId = ref<number | null>(null)
  const selectedImage = computed(() => images.value.find((i) => i.id === selectedId.value) ?? null)

  // Batches that are running, oldest first.
  const activeBatches = ref<T2iActiveBatch[]>([])
  // The latest batch_step of the running Random batch: the batch the server has
  // put on show (the one being rendered), which the dialog's read-only Caption /
  // Prompt / Aspect boxes mirror. Null when no Random batch is running.
  const currentStep = ref<T2iBatchStepEvent | null>(null)
  // The batch that ended most recently. The dialog unlocks from it: for a
  // Random batch its `step` fills Caption / Prompt / Aspect and `next_seed`
  // the Seed box. Reset to null when the next batch begins.
  const lastFinished = ref<T2iFinishedBatch | null>(null)

  const randomBatch = computed(() => activeBatches.value.find((b) => b.mode === 'random') ?? null)
  // The seed of the image the running Random batch is rendering: what the
  // locked Seed box shows, and it moves on after each image. Null until the
  // batch has reported one (the box then keeps the form's seed).
  const liveSeed = computed(() => randomBatch.value?.seed ?? null)
  // Anything still running that Cancel could stop.
  const isBusy = computed(
    () => activeBatches.value.length > 0 || tracker.activeJobIds.value.length > 0,
  )
  // "Batch 12/40 · 37/160 images · rendering" for the Random batch, else the
  // newest one, plus a count of the others.
  const statusText = computed(() => {
    const list = activeBatches.value
    if (list.length === 0) return ''
    const main = list.find((b) => b.mode === 'random') ?? list[list.length - 1]
    const others = list.length - 1
    return batchStatusLine(main) + (others > 0 ? ` (+${others} more)` : '')
  })

  // ---- bookkeeping -------------------------------------------------------------

  // Each request family numbers its calls, so a reply that arrives after a
  // newer request (or after open() started a fresh session) is dropped.
  let imagesSeq = 0
  let batchesSeq = 0
  let countSeq = 0
  let resolveSeq = 0
  // Batches that ended, so a late or replayed frame cannot bring one back.
  const finishedIds = new Set<string>()

  // ---- loading -------------------------------------------------------------

  // Each loader below reports its own failure (toast + error field) and
  // never rejects, so open() cannot leak an unhandled rejection.
  async function loadConfig(): Promise<T2iConfig | null> {
    configLoading.value = true
    try {
      const loaded = await t2iApi.fetchT2iConfig()
      config.value = loaded
      configError.value = null
      return loaded
    } catch (e) {
      configError.value = errorMessage(e)
      toast.show(`Couldn't load Text to Image settings: ${configError.value}`, 'warn', 5000)
      return null
    } finally {
      configLoading.value = false
    }
  }

  async function loadCaptionMeta(): Promise<CaptionMeta | null> {
    metaLoading.value = true
    try {
      const meta = await t2iApi.fetchCaptionMeta()
      captionMeta.value = meta
      metaError.value = null
      return meta
    } catch (e) {
      metaError.value = errorMessage(e)
      toast.show(`Couldn't load the caption filters: ${metaError.value}`, 'warn', 5000)
      return null
    } finally {
      metaLoading.value = false
    }
  }

  // Refetches the first page and folds it into the list. A reply that
  // arrives after a newer request was made is dropped.
  async function refreshImages(): Promise<void> {
    const seq = ++imagesSeq
    imagesLoading.value = true
    try {
      const fresh = await t2iApi.listT2iImages(IMAGE_PAGE_SIZE)
      if (seq !== imagesSeq) return
      const merged = mergeImagePage(images.value, fresh, IMAGE_PAGE_SIZE, hasMoreImages.value)
      images.value = merged.images
      hasMoreImages.value = merged.hasMore
      imagesLoaded.value = true
      imagesError.value = null
      // The selected image can vanish: deleted elsewhere, or pruned by the
      // server because its media row is gone.
      if (selectedId.value !== null && !merged.images.some((i) => i.id === selectedId.value)) {
        selectedId.value = null
      }
    } catch (e) {
      if (seq === imagesSeq) imagesError.value = errorMessage(e)
    } finally {
      if (seq === imagesSeq) imagesLoading.value = false
    }
  }

  async function loadOlder(): Promise<void> {
    if (!hasMoreImages.value || loadingOlder.value || images.value.length === 0) return
    loadingOlder.value = true
    const cursor = images.value[images.value.length - 1].id
    try {
      const page = await t2iApi.listT2iImages(IMAGE_PAGE_SIZE, cursor)
      images.value = mergeOlderPage(images.value, page)
      hasMoreImages.value = page.length >= IMAGE_PAGE_SIZE
    } catch (e) {
      toast.show(`Couldn't load older images: ${errorMessage(e)}`, 'warn')
    } finally {
      loadingOlder.value = false
    }
  }

  /** Opening the dialog: a fresh session over whatever the server is still running. */
  async function open(): Promise<void> {
    imagesSeq += 1
    batchesSeq += 1
    selectedId.value = null
    images.value = []
    hasMoreImages.value = false
    imagesLoaded.value = false
    imagesError.value = null
    activeBatches.value = []
    currentStep.value = null
    lastFinished.value = null
    finishedIds.clear()
    tracker.reset()
    const loaded = await loadConfig()
    await Promise.all([
      loaded?.csv.available ? loadCaptionMeta() : Promise.resolve(null),
      reattach(),
    ])
  }

  /** Closing does not stop batches: they run on the server. */
  function close(): void {
    selectedId.value = null
  }

  // ---- captions and prompts -----------------------------------------------------

  // Review-only. Errors propagate: the dialog words them (a 502 means the
  // VLM failed and the user retries).
  function generatePrompt(body: {
    caption: string
    seed: number
    model: string
  }): Promise<T2iPromptResult> {
    return t2iApi.generateT2iPrompt(body)
  }

  // One random CSV row. A 404 ApiError means nothing matches the filter.
  function rollCaption(filter: CaptionFilter): Promise<CaptionRow> {
    return t2iApi.randomCaption(cleanFilter(filter))
  }

  /**
   * How many CSV rows match. Debouncing is the caller's job. Null when the
   * request failed or a newer one was made meanwhile (leave the label alone).
   */
  async function countCaptions(filter: CaptionFilter): Promise<CaptionCount | null> {
    const seq = ++countSeq
    try {
      const result = await t2iApi.countCaptions(cleanFilter(filter))
      return seq === countSeq ? result : null
    } catch {
      return null
    }
  }

  /** The read-only "Resolved caption" preview. Null on failure or when superseded. */
  async function resolveCaption(body: {
    caption: string
    seed: number
    model: string
  }): Promise<T2iResolveResult | null> {
    const seq = ++resolveSeq
    try {
      const result = await t2iApi.resolveCaption(body)
      return seq === resolveSeq ? result : null
    } catch {
      return null
    }
  }

  // ---- batches -----------------------------------------------------------------

  function rememberFinished(id: string): void {
    finishedIds.add(id)
    if (finishedIds.size > FINISHED_IDS_CAP) {
      const oldest = finishedIds.values().next().value
      if (oldest !== undefined) finishedIds.delete(oldest)
    }
  }

  function findBatch(id: string): T2iActiveBatch | undefined {
    return activeBatches.value.find((b) => b.batch_id === id)
  }

  function beginBatch(batch: T2iActiveBatch): void {
    activeBatches.value.push(batch)
    lastFinished.value = null
    if (batch.mode === 'random') currentStep.value = null
  }

  // Moves a batch from "running" to `lastFinished`. Idempotent: an unknown or
  // already-finished id does nothing and returns null.
  function finishBatch(
    id: string,
    outcome: T2iBatchOutcome,
    over: { images_done?: number; images_failed?: number; error?: string | null } = {},
  ): T2iFinishedBatch | null {
    const batch = findBatch(id)
    if (!batch) return null
    activeBatches.value = activeBatches.value.filter((b) => b.batch_id !== id)
    rememberFinished(id)
    const finished: T2iFinishedBatch = {
      batch_id: id,
      mode: batch.mode,
      outcome,
      images_done: over.images_done ?? batch.images_done,
      images_failed: over.images_failed ?? batch.images_failed,
      next_seed: batch.next_seed,
      step: batch.step ? cloneJson(batch.step) : null,
      error: over.error ?? null,
    }
    lastFinished.value = finished
    if (currentStep.value?.batch_id === id) currentStep.value = null
    return finished
  }

  // Silent: a reload every few seconds must not flash the library's dimmed
  // "Loading media..." overlay.
  const scheduleMediaReload = useDebounceFn(
    () => {
      media
        .loadAllMedia({ silent: true })
        .catch((e) => console.warn('T2I: could not reload the library', e))
    },
    MEDIA_RELOAD_DEBOUNCE_MS,
    { maxWait: MEDIA_RELOAD_MAX_WAIT_MS },
  )

  // What a finished batch leaves to reconcile: the strip, the library grid
  // and the job chips (a missed frame must not strand one).
  function afterBatchEnded(): void {
    void refreshImages()
    void scheduleMediaReload()
    tracker.refresh().catch((e) => console.warn('T2I: could not refresh the job chips', e))
  }

  function applyBatchSnapshot(row: T2iBatchInfo): void {
    if (finishedIds.has(row.batch_id)) return
    const existing = findBatch(row.batch_id)
    if (existing) {
      existing.mode = row.mode
      existing.state = row.state
      existing.total_steps = row.total_steps
      if (row.step) existing.step = row.step
      existing.images_total = row.images_total
      // The snapshot may be older than a frame already handled: counters only grow.
      // The seed moves with the image count, so it is taken only from a snapshot
      // that has seen at least as many images as the frames already handled.
      if (row.images_done + row.images_failed >= existing.images_done + existing.images_failed) {
        existing.seed = row.seed
      }
      existing.images_done = Math.max(existing.images_done, row.images_done)
      existing.images_failed = Math.max(existing.images_failed, row.images_failed)
      existing.next_seed = existing.next_seed ?? row.next_seed
      existing.started_at = row.started_at
    } else {
      activeBatches.value.push({ ...row, last_error: null })
    }
    const cur = currentStep.value
    if (
      row.mode === 'random' &&
      row.step &&
      (!cur || cur.batch_id !== row.batch_id || row.step.step > cur.step)
    ) {
      currentStep.value = { ...row.step, batch_id: row.batch_id }
    }
  }

  /**
   * Reloads the running batches from the server. A batch held locally that
   * the server no longer lists has ended (its frames were missed) and moves
   * to `lastFinished`; one that began while the request was in flight is kept.
   */
  async function refreshBatches(): Promise<void> {
    const seq = ++batchesSeq
    const before = activeBatches.value.map((b) => b.batch_id)
    const rows = await t2iApi.listT2iBatches()
    if (seq !== batchesSeq) return
    const listed = new Set(rows.map((r) => r.batch_id))
    for (const id of before) {
      if (!listed.has(id)) finishBatch(id, 'complete')
    }
    for (const row of rows) applyBatchSnapshot(row)
  }

  /**
   * On open, and after a WebSocket reconnect: rebuild from the server
   * everything a missed frame could have changed (running batches, job
   * chips, the strip).
   */
  async function reattach(): Promise<void> {
    await Promise.all([
      refreshBatches().catch((e) => console.warn('T2I: could not reload the running batches', e)),
      tracker.refresh().catch((e) => console.warn('T2I: could not reload the running jobs', e)),
      refreshImages(),
    ])
  }

  // The next unused seed the server reported for a batch, asking once if no
  // frame has carried it yet. Null when there is none (e.g. Randomize).
  async function nextSeedFor(id: string): Promise<number | null> {
    const known = (): number | null =>
      findBatch(id)?.next_seed ??
      (lastFinished.value?.batch_id === id ? lastFinished.value.next_seed : null)
    const now = known()
    if (now !== null) return now
    try {
      await refreshBatches()
    } catch (e) {
      console.warn('T2I: could not read the next seed', e)
    }
    return known()
  }

  /**
   * Starts a server-side batch. Validation problems (400), a Random batch
   * already running (409) and ComfyUI failures (502) throw an ApiError for the
   * dialog to show. On success the batch is tracked at once and the result
   * carries `next_seed`, which the dialog puts in its Seed box.
   */
  async function startBatch(req: T2iBatchRequest): Promise<T2iStartResult> {
    const body: T2iBatchRequest = { ...req }
    if (req.filter) body.filter = cleanFilter(req.filter)
    const started = await t2iApi.startT2iBatch(body)
    // The WebSocket frames may have beaten this response, or the batch may
    // already be over: only a batch that is neither is added here.
    if (!findBatch(started.batch_id) && !finishedIds.has(started.batch_id)) {
      beginBatch({
        batch_id: started.batch_id,
        mode: req.mode,
        state: req.mode === 'random' ? 'prompting' : 'rendering',
        total_steps: req.mode === 'random' ? (req.batch_size ?? 1) : 1,
        step: null,
        images_total: started.total_images,
        images_done: 0,
        images_failed: 0,
        seed: req.seed, // the first image's, until the batch reports its own
        next_seed: null,
        started_at: new Date().toISOString(),
        last_error: null,
      })
    }
    return { ...started, next_seed: await nextSeedFor(started.batch_id) }
  }

  /**
   * Cancels every running batch (the server also cancels their jobs), then
   * any job chip still left, e.g. one whose batch died with a server restart.
   * Everything is attempted; the first failure is rethrown afterwards.
   */
  async function cancelAll(): Promise<void> {
    const ids = activeBatches.value.map((b) => b.batch_id)
    const failures: unknown[] = []
    const results = await Promise.allSettled(
      ids.map(async (id) => {
        await t2iApi.cancelT2iBatch(id)
        // The batch_cancelled frame normally does this; doing it here as
        // well means a missed frame cannot leave the dialog locked.
        finishBatch(id, 'cancelled')
      }),
    )
    for (const r of results) if (r.status === 'rejected') failures.push(r.reason)
    if (ids.length > 0) {
      try {
        await tracker.refresh()
      } catch (e) {
        console.warn('T2I: could not refresh the job chips after cancelling', e)
      }
    }
    try {
      await tracker.cancelAll()
    } catch (e) {
      failures.push(e)
    }
    if (failures.length > 0) throw failures[0]
  }

  // ---- WebSocket handlers ---------------------------------------------------------

  function onBatchStarted(d: Record<string, unknown>): void {
    const id = asStr(d.batch_id)
    if (!id || finishedIds.has(id)) return
    const totalSteps = asNum(d.total_steps, 1)
    const totalImages = asNum(d.total_images, 0)
    const existing = findBatch(id)
    if (existing) {
      existing.total_steps = totalSteps
      existing.images_total = totalImages
      return
    }
    const mode: T2iMode = d.mode === 'random' ? 'random' : 'manual'
    beginBatch({
      batch_id: id,
      mode,
      state: mode === 'random' ? 'prompting' : 'rendering',
      total_steps: totalSteps,
      step: null,
      images_total: totalImages,
      images_done: 0,
      images_failed: 0,
      seed: null,
      next_seed: null,
      started_at: new Date().toISOString(),
      last_error: null,
    })
  }

  function onBatchStep(d: Record<string, unknown>): void {
    const batch = findBatch(asStr(d.batch_id))
    if (!batch) return
    const step: T2iBatchStepEvent = {
      batch_id: batch.batch_id,
      step: asNum(d.step, 1),
      total_steps: asNum(d.total_steps, batch.total_steps),
      caption: asStr(d.caption),
      aspect_ratio: asStr(d.aspect_ratio),
      seed: asNum(d.seed, 0),
      prompt: asStr(d.prompt),
      negative: d.negative == null ? null : asStr(d.negative),
      warnings: Array.isArray(d.warnings) ? d.warnings.map(String) : [],
    }
    batch.step = step
    batch.total_steps = step.total_steps
    // Only a Random batch drives the live boxes; a Manual step just echoes the form.
    if (batch.mode === 'random') currentStep.value = step
  }

  function onBatchProgress(d: Record<string, unknown>): void {
    const batch = findBatch(asStr(d.batch_id))
    if (!batch) return
    if (d.phase === 'prompting' || d.phase === 'rendering') batch.state = d.phase
    batch.images_done = asNum(d.images_done, batch.images_done)
    batch.images_failed = asNum(d.images_failed, batch.images_failed)
    batch.images_total = asNum(d.images_total, batch.images_total)
    if ('seed' in d) batch.seed = asNumOrNull(d.seed)
    if ('next_seed' in d) batch.next_seed = asNumOrNull(d.next_seed)
    const lastError = asStr(d.last_error)
    if (lastError) batch.last_error = lastError
  }

  function onBatchEnd(d: Record<string, unknown>, outcome: T2iBatchOutcome): void {
    const finished = finishBatch(asStr(d.batch_id), outcome, {
      images_done: typeof d.images_done === 'number' ? d.images_done : undefined,
      images_failed: typeof d.images_failed === 'number' ? d.images_failed : undefined,
    })
    if (finished) afterBatchEnded()
  }

  function onBatchError(d: Record<string, unknown>): void {
    const error = asStr(d.error, 'the batch failed')
    const finished = finishBatch(asStr(d.batch_id), 'error', { error })
    if (!finished) return
    toast.show(`Text to Image batch failed: ${error}`, 'warn', 6000)
    afterBatchEnded()
  }

  /**
   * The `t2i` channel. Frames for a batch this store does not know (or that
   * already ended) are ignored, except batch_started.
   */
  function handleT2iEvent(event: string, data: Record<string, unknown>): void {
    switch (event) {
      case 'batch_started':
        return onBatchStarted(data)
      case 'batch_step':
        return onBatchStep(data)
      case 'batch_progress':
        return onBatchProgress(data)
      case 'batch_complete':
        return onBatchEnd(data, 'complete')
      case 'batch_cancelled':
        return onBatchEnd(data, 'cancelled')
      case 'batch_error':
        return onBatchError(data)
      case 't2i_images_changed':
        void refreshImages()
        void scheduleMediaReload()
        return
    }
  }

  /** The `comfy` channel: job_update / job_progress for the job chips. */
  function handleComfyEvent(event: string, data: Record<string, unknown>): void {
    tracker.handleComfyEvent(event, data)
  }

  // ---- selection, autosave, favourite, delete -----------------------------------------

  /**
   * Selects an image and returns a copy of its (complete) form_state for the
   * dialog to load; null, with the selection untouched, for an unknown id.
   */
  function selectImage(id: number): T2iFormState | null {
    const image = images.value.find((i) => i.id === id)
    if (!image) return null
    selectedId.value = id
    return cloneJson(image.form_state)
  }

  /** Back to the scratch form ("Stop editing"). */
  function clearSelection(): void {
    selectedId.value = null
  }

  /**
   * Autosave target: writes the image's EDITABLE form_state only, then
   * mirrors the server's merged row locally so selecting the tile again loads
   * what was just saved. An empty patch sends nothing (the server refuses it).
   */
  async function saveFormState(id: number, fields: Partial<T2iFormState>): Promise<void> {
    if (Object.keys(fields).length === 0) return
    const row = await t2iApi.patchT2iImage(id, fields)
    const index = images.value.findIndex((i) => i.id === id)
    if (index >= 0) images.value[index] = row
  }

  function mirrorFavoriteToLibrary(path: string, favorite: boolean): void {
    const entry = media.allMedia.find((m) => samePath(m.file_path, path))
    if (entry) entry.is_favorite = favorite
    if (media.selectedMedia && samePath(media.selectedMedia.file_path, path)) {
      media.selectedMedia.is_favorite = favorite
    }
  }

  /** Optimistic; the library copy is updated too. Reverts and rethrows on failure. */
  async function toggleFavorite(id: number): Promise<void> {
    const image = images.value.find((i) => i.id === id)
    if (!image) return
    const next = !image.is_favorite
    image.is_favorite = next
    try {
      const updated = await updateMedia(image.file_path, { is_favorite: next })
      image.is_favorite = updated.is_favorite
      mirrorFavoriteToLibrary(image.file_path, updated.is_favorite)
    } catch (e) {
      image.is_favorite = !next
      throw e
    }
  }

  function removeFromLibrary(path: string): void {
    media.allMedia = media.allMedia.filter((m) => !samePath(m.file_path, path))
    if (media.selectedMedia && samePath(media.selectedMedia.file_path, path)) {
      media.selectedMedia = null
    }
    // A manual folder must not keep a member whose file is gone.
    folders.purgePath(path)
  }

  /**
   * Deletes the image (the server moves its file to the OS trash) and drops
   * it from the strip, the library grid and manual folders. A failure throws
   * and changes nothing. Does not touch the smart-folder path caches.
   */
  async function deleteImage(id: number): Promise<void> {
    const image = images.value.find((i) => i.id === id)
    await t2iApi.deleteT2iImage(id)
    images.value = images.value.filter((i) => i.id !== id)
    if (selectedId.value === id) selectedId.value = null
    if (image) removeFromLibrary(image.file_path)
    // Pulls one older row into the gap when there is a next page.
    void refreshImages()
  }

  // ---- scratch-form draft -------------------------------------------------------------

  // A per-viewer convenience: every access is guarded, and the dialog works
  // the same without it (private window, blocked or full storage).

  function loadDraft(): Partial<T2iFormState> | null {
    try {
      const raw = localStorage.getItem(DRAFT_STORAGE_KEY)
      return raw ? sanitizeDraft(JSON.parse(raw)) : null
    } catch {
      return null
    }
  }

  function saveDraft(form: Partial<T2iFormState>): void {
    try {
      localStorage.setItem(DRAFT_STORAGE_KEY, JSON.stringify(form))
    } catch {
      // Not persisted; nothing depends on it.
    }
  }

  function clearDraft(): void {
    try {
      localStorage.removeItem(DRAFT_STORAGE_KEY)
    } catch {
      // Nothing to clear.
    }
  }

  return {
    // state
    config,
    configLoading,
    configError,
    captionMeta,
    metaLoading,
    metaError,
    images,
    hasMoreImages,
    imagesLoaded,
    imagesLoading,
    imagesError,
    loadingOlder,
    selectedId,
    selectedImage,
    activeBatches,
    randomBatch,
    liveSeed,
    currentStep,
    lastFinished,
    isBusy,
    statusText,
    jobs: tracker.jobs,
    activeJobIds: tracker.activeJobIds,
    // loading
    open,
    close,
    // The one debounced library-grid reload. App.vue's always-on `t2i` bridge
    // calls it too, so images a batch finishes while the dialog is closed
    // still reach the grid, and an open dialog does not reload twice.
    scheduleMediaReload,
    loadConfig,
    loadCaptionMeta,
    refreshImages,
    loadOlder,
    reattach,
    refreshBatches,
    // captions and prompts
    generatePrompt,
    rollCaption,
    countCaptions,
    resolveCaption,
    // batches
    startBatch,
    cancelAll,
    dismissJob: tracker.dismiss,
    cancelJob: tracker.cancel,
    handleT2iEvent,
    handleComfyEvent,
    // selection and per-image actions
    selectImage,
    clearSelection,
    saveFormState,
    toggleFavorite,
    deleteImage,
    // draft
    loadDraft,
    saveDraft,
    clearDraft,
  }
})
