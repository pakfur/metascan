import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import type { Media } from '../types/media'
import type { JudgmentState } from '../types/judgments'
import type {
  I2vBatch,
  I2vDraft,
  I2vConfig,
  I2vFormState,
  I2vJobChip,
  I2vTemplate,
  I2vVideo,
} from '../types/i2v'
import {
  cancelI2vBatch,
  fetchI2vConfig,
  listAllI2vVideos,
  listI2vBatches,
  listI2vTemplates,
  listI2vVideos,
  updateI2vFormState,
} from '../api/i2v'
import { cancelJob as apiCancelJob, listJobs } from '../api/comfy'
import { setFavorite } from '../api/judgments'
import { listWriterAdapters } from '../api/writers'
import type { WriterAdapter } from '../types/writers'
import { makeRowNotes } from '../utils/rowNotes'
import { mergeNewestPage } from '../utils/i2vLists'

// Rows per page of the all-videos list.
const ALL_PAGE = 60

// POSIX/native tolerance: job rows store POSIX, Media paths are native.
function norm(p: string): string {
  return p.replace(/\\/g, '/')
}

export const useI2vStore = defineStore('i2v', () => {
  // The open source image's path, as given (native or POSIX). `source` is
  // its Media row, or null while the path is set and its image was deleted.
  const sourcePath = ref<string | null>(null)
  const source = ref<Media | null>(null)
  // The open source's clips (left list) and every clip, newest first (right).
  const videos = ref<I2vVideo[]>([])
  const allVideos = ref<I2vVideo[]>([])
  const allVideosLoaded = ref(false)
  const hasMoreAll = ref(false)
  // Running batch runs of every source; the dialog shows `sourceBatches`.
  const batches = ref<I2vBatch[]>([])
  // Writer warnings from batch events, and the note a failed run leaves.
  const warnings = ref<string[]>([])
  const statusNote = ref<string | null>(null)
  const jobs = ref<Map<number, I2vJobChip>>(new Map())
  const config = ref<I2vConfig | null>(null)
  // The découpage library. Loaded once per open(); a failure leaves it
  // empty and the dialog offers only Single take.
  const templates = ref<I2vTemplate[]>([])

  // The writer's draft behind the Prompt box, or null (a prompt typed from
  // scratch). Held here, never written into the form by a watcher.
  const draft = ref<I2vDraft | null>(null)

  function setDraft(d: I2vDraft | null): void {
    draft.value = d
  }

  // Prompt-writer adapters for the Writer menu (GET /api/writers/adapters),
  // refetched on the `training` channel's adapters_changed. A failed load
  // keeps the last list; `adaptersLoaded` says whether one ever arrived (a
  // selected adapter is only judged gone against a list that did).
  const adapters = ref<WriterAdapter[]>([])
  const adaptersLoaded = ref(false)
  let adaptersSeq = 0
  async function loadAdapters(): Promise<void> {
    const seq = ++adaptersSeq
    try {
      const rows = await listWriterAdapters()
      if (seq !== adaptersSeq) return
      adapters.value = rows
      adaptersLoaded.value = true
    } catch {
      // The menus keep what they had; a request naming a gone adapter 409s.
    }
  }

  async function open(media: Media): Promise<void> {
    sourcePath.value = media.file_path
    source.value = media
    videosSeq += 1
    jobsSeq += 1
    draft.value = null
    jobs.value = new Map()
    videos.value = []
    batches.value = []
    warnings.value = []
    statusNote.value = null
    config.value = await fetchI2vConfig()
    try {
      templates.value = await listI2vTemplates()
    } catch {
      templates.value = []
    }
    // The right-hand list and the runs are extras: their failure leaves them
    // empty rather than the dialog without its defaults.
    await Promise.all([
      refreshVideos(),
      refreshActiveJobs(),
      loadAdapters(),
      refreshAllVideos().catch(() => {}),
      refreshBatches().catch(() => {}),
    ])
  }

  function close(): void {
    sourcePath.value = null
    source.value = null
    videosSeq += 1
    jobsSeq += 1
    allSeq += 1
    batchesSeq += 1
    draft.value = null
    videos.value = []
    allVideos.value = []
    allVideosLoaded.value = false
    hasMoreAll.value = false
    batches.value = []
    warnings.value = []
    statusNote.value = null
    jobs.value = new Map()
  }

  // Show another source image: `media` is null when the path is a clip's
  // source whose image is gone from the library. Replies still out for the
  // previous source are dropped.
  function switchSource(path: string, media: Media | null): void {
    sourcePath.value = path
    source.value = media
    videosSeq += 1
    jobsSeq += 1
    batchesSeq += 1
    jobs.value = new Map()
    videos.value = []
    batches.value = []
    warnings.value = []
    statusNote.value = null
    for (const p of [refreshVideos(), refreshActiveJobs(), refreshBatches()]) p.catch(() => {})
  }

  // The ★ and grades the server answered (utils/rowNotes.ts): the clips
  // a reply brings back keep each one answered after the request went out.
  const notes = makeRowNotes<I2vVideo>()
  let videosSeq = 0
  let jobsSeq = 0
  let allSeq = 0
  let batchesSeq = 0

  // A reply that arrives after a newer refresh started, or after the dialog
  // moved to another source image, is dropped.
  async function refreshVideos(): Promise<void> {
    if (!sourcePath.value) return
    const seq = ++videosSeq
    const askedAt = notes.ask()
    try {
      const rows = await listI2vVideos(sourcePath.value)
      if (seq !== videosSeq) return
      for (const row of rows) notes.settle(norm(row.file_path), row, askedAt)
      videos.value = rows
    } finally {
      notes.done(askedAt)
    }
  }

  // The all-videos list: a fresh newest page is folded into what is held, so
  // pages "load older" fetched stay. Superseded replies are dropped.
  async function refreshAllVideos(): Promise<void> {
    const seq = ++allSeq
    const askedAt = notes.ask()
    try {
      const rows = await listAllI2vVideos(ALL_PAGE, null)
      if (seq !== allSeq) return
      for (const row of rows) notes.settle(norm(row.file_path), row, askedAt)
      const full = rows.length >= ALL_PAGE
      const merged = full ? mergeNewestPage(rows, allVideos.value) : rows
      allVideos.value = merged
      // A short page is the whole list. A full one leaves older rows unknown
      // unless the merge kept some held ones beyond it (then the flag stands):
      // a list that grew past one page since it was short has more.
      if (!full) hasMoreAll.value = false
      else hasMoreAll.value = hasMoreAll.value || merged.length === rows.length
      allVideosLoaded.value = true
    } finally {
      notes.done(askedAt)
    }
  }

  async function loadOlderVideos(): Promise<void> {
    const last = allVideos.value[allVideos.value.length - 1]
    if (!last || !hasMoreAll.value) return
    const seq = allSeq
    const askedAt = notes.ask()
    try {
      const rows = await listAllI2vVideos(ALL_PAGE, last.id)
      if (seq !== allSeq) return
      for (const row of rows) notes.settle(norm(row.file_path), row, askedAt)
      const held = new Set(allVideos.value.map((v) => v.id))
      allVideos.value = [...allVideos.value, ...rows.filter((v) => !held.has(v.id))]
      hasMoreAll.value = rows.length >= ALL_PAGE
    } finally {
      notes.done(askedAt)
    }
  }

  // A clip deleted here leaves both lists at once.
  function removeVideo(id: number): void {
    videos.value = videos.value.filter((v) => v.id !== id)
    allVideos.value = allVideos.value.filter((v) => v.id !== id)
  }

  // Reload-survival: rebuild the in-flight set from the server, filtered
  // to this source image (the storyboard refreshActiveJobs pattern).
  async function refreshActiveJobs(): Promise<void> {
    if (!sourcePath.value) return
    const seq = ++jobsSeq
    const src = norm(sourcePath.value)
    const [queued, running] = await Promise.all([
      listJobs('queued', 1000),
      listJobs('running', 1000),
    ])
    if (seq !== jobsSeq) return
    const next = new Map<number, I2vJobChip>()
    for (const j of [...queued, ...running]) {
      if (j.i2v_source_path && norm(j.i2v_source_path) === src) {
        next.set(j.id, { state: j.state === 'running' ? 'running' : 'queued' })
      }
    }
    jobs.value = next
  }

  // The server's running runs, replacing the list wholesale: a run that
  // ended while no frame reached us leaves nothing behind.
  // Frames that arrived while the request was out are newer than its reply:
  // a run that ended meanwhile stays gone, one that moved keeps its frame,
  // and one a frame added is kept even if the reply lacks it.
  let batchTick = 0
  const batchEnded = new Map<string, number>()
  const batchTouched = new Map<string, number>()
  function stamp(m: Map<string, number>, id: string): void {
    m.delete(id)
    m.set(id, ++batchTick)
    while (m.size > 200) m.delete(m.keys().next().value as string)
  }

  async function refreshBatches(): Promise<void> {
    const seq = ++batchesSeq
    const askedAt = ++batchTick
    const rows = await listI2vBatches()
    if (seq !== batchesSeq) return
    const current = new Map(batches.value.map((b) => [b.batch_id, b]))
    const next: I2vBatch[] = []
    for (const row of rows) {
      if ((batchEnded.get(row.batch_id) ?? 0) > askedAt) continue
      const newer = (batchTouched.get(row.batch_id) ?? 0) > askedAt ? current.get(row.batch_id) : undefined
      next.push(newer ?? row)
    }
    for (const [id, row] of current) {
      if ((batchTouched.get(id) ?? 0) > askedAt && !next.some((b) => b.batch_id === id)) next.push(row)
    }
    batches.value = next
  }

  function upsertBatch(row: I2vBatch): void {
    stamp(batchTouched, row.batch_id)
    const i = batches.value.findIndex((b) => b.batch_id === row.batch_id)
    if (i < 0) batches.value = [...batches.value, row]
    else batches.value = batches.value.map((b, j) => (j === i ? row : b))
  }

  // A run just started here, shown before its first frame arrives.
  function trackBatch(row: I2vBatch): void {
    upsertBatch(row)
  }

  const sourceBatches = computed<I2vBatch[]>(() => {
    const src = sourcePath.value
    if (!src) return []
    return batches.value.filter((b) => norm(b.source_path) === norm(src))
  })

  function trackJob(jobId: number): void {
    const next = new Map(jobs.value)
    next.set(jobId, { state: 'queued' })
    jobs.value = next
  }

  // Jobs that can still be cancelled (a failed tile is only dismissable).
  const activeJobIds = computed<number[]>(() =>
    [...jobs.value].filter(([, c]) => c.state !== 'failed').map(([id]) => id),
  )

  function patchChip(jobId: number, fields: Partial<I2vJobChip>): void {
    const chip = jobs.value.get(jobId)
    if (!chip) return
    const next = new Map(jobs.value)
    next.set(jobId, { ...chip, ...fields })
    jobs.value = next
  }

  // Cancel one queued/running job. The tile normally leaves via the comfy
  // channel's job_update → cancelled; it is also dropped here on success
  // so a missed WS frame cannot strand it. On failure the tile stays, with
  // its button re-armed, and the error propagates to the caller's toast.
  async function cancelJob(jobId: number): Promise<void> {
    const chip = jobs.value.get(jobId)
    if (!chip || chip.state === 'failed' || chip.cancelling) return
    patchChip(jobId, { cancelling: true })
    try {
      await apiCancelJob(jobId)
      dismissJob(jobId)
    } catch (e) {
      patchChip(jobId, { cancelling: false })
      throw e
    }
  }

  // Cancel every in-flight job for this source image. Every job is
  // attempted; the first failure is rethrown after the rest have settled.
  async function cancelAllJobs(): Promise<void> {
    const results = await Promise.allSettled(activeJobIds.value.map((id) => cancelJob(id)))
    const failed = results.find((r): r is PromiseRejectedResult => r.status === 'rejected')
    if (failed) throw failed.reason
  }

  // Cancel the open source's runs (stops their writing and cancels their
  // jobs), then every job still in flight for it. Every attempt runs; the
  // first failure is rethrown after the rest have settled.
  async function cancelSourceRuns(): Promise<void> {
    const results = await Promise.allSettled(
      sourceBatches.value.map((b) => cancelI2vBatch(b.batch_id)),
    )
    try {
      await cancelAllJobs()
    } catch (e) {
      results.push({ status: 'rejected', reason: e })
    }
    const failed = results.find((r): r is PromiseRejectedResult => r.status === 'rejected')
    if (failed) throw failed.reason
  }

  function dismissJob(jobId: number): void {
    const next = new Map(jobs.value)
    next.delete(jobId)
    jobs.value = next
  }

  // comfy channel: job_update / job_progress for jobs we track.
  function handleComfyEvent(event: string, data: Record<string, unknown>): void {
    const jobId = Number(data.job_id)
    if (!jobs.value.has(jobId)) return
    const next = new Map(jobs.value)
    if (event === 'job_update') {
      const state = String(data.state)
      if (state === 'failed') {
        next.set(jobId, { state: 'failed', error: (data.error as string) ?? null })
      } else if (state === 'done' || state === 'cancelled') {
        next.delete(jobId)
      } else {
        next.set(jobId, {
          state: state === 'running' ? 'running' : 'queued',
          cancelling: next.get(jobId)?.cancelling,
        })
      }
    } else if (event === 'job_progress') {
      next.set(jobId, {
        state: 'running',
        value: Number(data.value),
        max: Number(data.max),
        cancelling: next.get(jobId)?.cancelling,
      })
    }
    jobs.value = next
  }

  // i2v channel: clip lists and the running batches.
  function handleI2vEvent(event: string, data: Record<string, unknown>): void {
    if (event === 'i2v_videos_changed') {
      refreshAllVideos().catch(() => {})
      if (sourcePath.value && norm(String(data.source_path)) === norm(sourcePath.value)) {
        refreshVideos().catch(() => {})
      }
      return
    }
    if (!event.startsWith('batch_')) return
    const row: I2vBatch = {
      batch_id: String(data.batch_id),
      source_path: String(data.source_path),
      phase: data.phase === 'writing' ? 'writing' : 'rendering',
      batch: Number(data.batch),
      total_batches: Number(data.total_batches),
      queued: Number(data.queued),
      total_videos: Number(data.total_videos),
    }
    const mine = !!sourcePath.value && norm(row.source_path) === norm(sourcePath.value)
    if (mine && Array.isArray(data.warnings)) {
      const fresh = (data.warnings as string[]).filter((w) => !warnings.value.includes(w))
      if (fresh.length) warnings.value = [...warnings.value, ...fresh]
    }
    if (event === 'batch_progress') {
      upsertBatch(row)
      return
    }
    if (event === 'batch_done' || event === 'batch_failed' || event === 'batch_cancelled') {
      stamp(batchEnded, row.batch_id)
      batches.value = batches.value.filter((b) => b.batch_id !== row.batch_id)
      if (event === 'batch_failed' && mine) {
        statusNote.value = `A run stopped: ${String(data.error ?? 'unknown error')}`
      }
    }
  }

  // A clip can sit in both lists (as two row objects); updates reach each.
  function bothLists(): I2vVideo[] {
    return [...videos.value, ...allVideos.value]
  }

  // ★ only: never the grade, its reasons or the scorecard. The answer is
  // applied by path: a refresh may have replaced this row meanwhile.
  async function toggleFavorite(video: I2vVideo): Promise<void> {
    const path = video.file_path
    const nextVal = !video.is_favorite
    video.is_favorite = nextVal // optimistic
    for (const v of bothLists()) if (norm(v.file_path) === norm(path)) v.is_favorite = nextVal
    let state: JudgmentState
    try {
      state = await setFavorite(path, nextVal)
    } catch (e) {
      for (const v of bothLists()) if (norm(v.file_path) === norm(path)) v.is_favorite = !nextVal
      throw e
    }
    applyJudgment(path, state)
  }

  // Follow a ★ the server returned from the result viewer's F / ☆ so the
  // strip's star matches.
  function applyFavoriteFlag(path: string, favorite: boolean): void {
    notes.note(norm(path), { is_favorite: favorite })
    for (const v of bothLists()) {
      if (norm(v.file_path) === norm(path) && v.is_favorite !== favorite) {
        v.is_favorite = favorite
      }
    }
  }

  // Follow every judgment the result viewer gets back (F / P / X): ★ and the
  // grade, so the strip's star and ✓ / ✗ badge match. Noted, so a reply asked
  // for before it cannot undo it.
  function applyJudgment(path: string, state: Pick<JudgmentState, 'favorite' | 'grade'>): void {
    notes.note(norm(path), { is_favorite: state.favorite, grade: state.grade })
    for (const v of bothLists()) {
      if (norm(v.file_path) !== norm(path)) continue
      if (v.is_favorite !== state.favorite) v.is_favorite = state.favorite
      if ((v.grade ?? null) !== state.grade) v.grade = state.grade
    }
  }

  // Autosave target for the dialog. Writes the clip's EDITABLE form state
  // only, then mirrors the server's merged result onto the local row so a
  // later click on that clip loads what was just saved.
  async function saveFormState(id: number, fields: Partial<I2vFormState>): Promise<void> {
    const res = await updateI2vFormState(id, fields)
    for (const v of bothLists()) if (v.id === id) v.form_state = res.form_state
  }

  return {
    adapters,
    adaptersLoaded,
    loadAdapters,
    sourcePath,
    source,
    videos,
    allVideos,
    allVideosLoaded,
    hasMoreAll,
    batches,
    sourceBatches,
    warnings,
    statusNote,
    jobs,
    config,
    templates,
    draft,
    setDraft,
    open,
    close,
    switchSource,
    refreshVideos,
    refreshAllVideos,
    loadOlderVideos,
    removeVideo,
    refreshBatches,
    trackBatch,
    cancelSourceRuns,
    refreshActiveJobs,
    trackJob,
    dismissJob,
    activeJobIds,
    cancelJob,
    cancelAllJobs,
    handleComfyEvent,
    handleI2vEvent,
    toggleFavorite,
    applyFavoriteFlag,
    applyJudgment,
    saveFormState,
  }
})
