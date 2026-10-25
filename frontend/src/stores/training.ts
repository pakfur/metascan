import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { ApiError } from '../api/client'
import {
  cancelBriefs,
  cancelTraining,
  fetchBriefsStatus,
  fetchTrainingProfiles,
  fetchTrainingStatus,
  pauseTraining,
  resumeTraining,
  runBriefs,
  startTraining,
} from '../api/training'
import {
  cancelScorecard as cancelScorecardApi,
  deleteWriterAdapter,
  fetchScorecards,
  listWriterAdapters,
  renameWriterAdapter,
  renderScorecard as renderScorecardApi,
} from '../api/writers'
import type {
  BriefsStatus,
  ResumableRun,
  StartTrainingRequest,
  TrainingProfiles,
  TrainingProgress,
  TrainingStatus,
} from '../types/training'
import { adapterProfileStats, formatEta, progressPct } from '../types/training'
import type { ScorecardProgress, ScorecardSummary, WriterAdapter } from '../types/writers'
import { GPU_BUSY_TEXT } from '../utils/gpuBusy'

const GPU_BUSY_REASONS: Record<string, string> = {
  comfy_jobs: 'ComfyUI jobs are queued or running',
  t2i_batch: 'a T2I batch is running',
  storyboard: 'a storyboard job is running',
  index_build: 'the embedding index is being built',
  upscale_active: 'upscaling is in progress',
  scorecard: 'a scorecard is rendering',
}

/** Human text for a training / adapter / briefs refusal (ApiError detail.code). */
export function trainingErrorText(e: unknown): string {
  if (!(e instanceof ApiError)) return e instanceof Error ? e.message : String(e)
  const d = (e.detail ?? {}) as Record<string, unknown>
  const code = typeof d.code === 'string' ? d.code : ''
  const list = (v: unknown): string[] => (Array.isArray(v) ? v.map(String) : [])
  switch (code) {
    case 'gpu_busy': {
      const why = list(d.reasons).map((r) => GPU_BUSY_REASONS[r] ?? r)
      return `The GPU is busy${why.length ? `: ${why.join('; ')}` : ''}. Training starts once it is free — try again then.`
    }
    case 'gpu_busy_training':
      return GPU_BUSY_TEXT
    case 'already_training':
      return 'A training run is already in progress.'
    case 'not_ready': {
      const missing = Number((d.readiness as { missing?: unknown } | undefined)?.missing ?? 0)
      return missing > 0
        ? `Not enough training data yet: ${missing} more preference or edit pair${missing === 1 ? '' : 's'} needed.`
        : 'Not enough training data yet.'
    }
    case 'gate_unavailable': {
      const why = list(d.reasons)
      return `Training is not available on this machine${why.length ? `: ${why.join(' ')}` : '.'}`
    }
    case 'gate_checking':
      return 'Checking training setup… try again in a moment.'
    case 'run_discarded':
      return 'That run was discarded (its adapter was deleted) and cannot be resumed.'
    case 'worker_unverifiable':
      return typeof d.message === 'string'
        ? d.message
        : `A training worker from an earlier server is still running (PID ${String(d.pid ?? '?')}); stop it yourself, then resume.`
    case 'worker_alive':
      return 'The previous training worker is still shutting down — try again in a few seconds.'
    case 'not_resumable':
      return `That run cannot be resumed (${String(d.run_status ?? 'unknown state')}).`
    case 'not_running':
      return 'No training run is active.'
    case 'not_cancellable':
      return `That run cannot be cancelled (${String(d.run_status ?? 'unknown state')}).`
    case 'adapter_not_continuable':
      return 'That adapter cannot be continued (only ready / untested adapters with their training files can).'
    case 'unknown_adapter':
      return 'That adapter no longer exists.'
    case 'unknown_scope':
      return 'That category no longer exists.'
    case 'invalid_hyperparams':
      return `Invalid Advanced settings${typeof d.message === 'string' ? `: ${d.message}` : '.'}`
    case 'invalid_vram_cap':
      return `The VRAM cap must be between ${String(d.min ?? 8)} GB and the card's memory (or Off).`
    case 'invalid_effort':
      return 'Unknown effort preset.'
    case 'training_unavailable':
      return 'Training is not set up on this server.'
    case 'adapter_training':
      return 'That adapter is training — cancel the run to remove it.'
    case 'adapter_files_busy':
      return 'The prompt writer still has that adapter loaded; try again once it is idle.'
    case 'duplicate_name':
      return 'Another adapter already has that name.'
    case 'invalid_name':
      return 'That name is not allowed.'
    case 'training_uses_base':
      return typeof d.message === 'string' ? d.message : 'A training run still uses the base weights.'
    case 'briefs_running':
      return 'Briefs are already being written.'
    case 'vlm_unavailable':
      return 'No VLM is available to write briefs — install one in Models.'
    case 'scorecard_running':
      return 'A scorecard is already rendering — wait for it or cancel it.'
    case 'scorecard_pending':
      return typeof d.message === 'string' && d.message
        ? d.message
        : 'Some briefs are still rendering — try again when they finish.'
    case 'shutting_down':
      return 'The server is shutting down.'
    case 'scorecard_unavailable':
      return 'Scorecards are not set up on this server.'
    case 'adapter_unavailable':
      return 'That adapter cannot write prompts (only ready / untested text-to-image adapters can).'
    case 'no_workflow':
      return `No workflow is set for ${String(d.model ?? 'that model')}: pick one in Settings → T2I.`
    case 'no_briefs':
      return 'The scorecard briefs file is missing or empty.'
    case 'unknown_model':
      return `Unknown target model ${String(d.model ?? '')}.`
    case 'writer_unavailable':
      return 'No prompt writer is available: install the writer model in Models.'
    case 'writer_failed':
      return 'The prompt writer failed — try again.'
    case 'adapter_loading':
      return 'The writer is still loading the adapter — try again in a moment.'
  }
  return e.message
}

const ACTIVE_STATES = new Set(['starting', 'running', 'pausing', 'cancelling'])
const MAX_LOSS_POINTS = 400

export const useTrainingStore = defineStore('training', () => {
  const status = ref<TrainingStatus | null>(null)
  const adapters = ref<WriterAdapter[]>([])
  const briefs = ref<BriefsStatus | null>(null)
  const windowOpen = ref(false)
  /** The scope the window's dataset panel shows ('all' or a category id). */
  const scope = ref('all')
  /** Loss per progress update of `lossRunId` (WS run_progress), for the sparkline. */
  const lossRunId = ref<number | null>(null)
  const lossPoints = ref<number[]>([])
  /** The last run outcome (completed / failed / cancelled / paused), shown in the window. */
  const lastOutcome = ref<{ kind: 'completed' | 'failed' | 'cancelled' | 'paused'; text: string } | null>(null)
  const loadError = ref<string | null>(null)
  /** Scorecard results per adapter + the base writer (loaded with the adapters). */
  const scorecards = ref<ScorecardSummary | null>(null)
  /** The scorecard rendering now (WS scorecard_progress), or null. */
  const scorecardRun = ref<ScorecardProgress | null>(null)
  /** The last scorecard outcome line, shown in the window. */
  const scorecardOutcome = ref<string | null>(null)
  /** Training profiles (VRAM / time per attempt; numbers only), loaded while the window is open. */
  const profiles = ref<TrainingProfiles | null>(null)
  const profileStats = computed(() => adapterProfileStats(profiles.value?.profiles ?? []))

  const run = computed(() => status.value?.run ?? null)
  const runActive = computed(() => !!run.value && ACTIVE_STATES.has(run.value.state))
  /** Training holds the GPU: generation, tagging, chat and search answer gpu_busy_training. */
  const locked = computed(() => !!status.value?.gpu_locked || runActive.value)
  const resumable = computed<ResumableRun[]>(() => status.value?.runs ?? [])

  const pct = computed(() => progressPct(run.value?.progress))
  /** `◐ Training <name> · <pct>% · ~<eta> left` — null when no run is active. */
  const chipText = computed<string | null>(() => {
    const r = run.value
    if (!r || !runActive.value) return null
    if (r.state === 'pausing') return `◐ Pausing ${r.name}…`
    if (r.state === 'cancelling') return `◐ Cancelling ${r.name}…`
    const parts = [`◐ Training ${r.name}`]
    if (pct.value !== null) parts.push(`${pct.value}%`)
    const eta = formatEta(r.progress?.eta_s)
    if (eta) parts.push(`~${eta} left`)
    return parts.join(' · ')
  })

  // A WS burst can start several loads; only the newest may write. Light
  // loads (window closed: no dataset build) and full loads (window open)
  // share one sequence for the run/state fields, and a full load's
  // dataset / next_name are written only by the newest full load.
  let loadSeq = 0
  let writtenSeq = 0
  let latestFull = 0
  async function load(recheck = false): Promise<void> {
    const full = windowOpen.value
    const token = ++loadSeq
    if (full) latestFull = token
    const wantScope = scope.value
    try {
      const data = await fetchTrainingStatus(wantScope, recheck, full)
      const fresh = token > writtenSeq
      const freshFull = full && token === latestFull
      if (!fresh && !freshFull) return
      const prev = status.value
      const base: TrainingStatus = fresh || !prev ? data : prev
      // A progress event may be newer than the status snapshot.
      const live = prev?.run
      if (fresh && data.run && live && live.run_id === data.run.run_id) {
        const a = live.progress?.updated_at ?? ''
        const b = data.run.progress?.updated_at ?? ''
        if (a > b) data.run.progress = live.progress
      }
      status.value = {
        ...base,
        dataset: freshFull ? data.dataset : prev?.dataset,
        next_name: freshFull ? data.next_name : prev?.next_name,
      }
      if (fresh) writtenSeq = token
      if (token === loadSeq) loadError.value = null
    } catch (e) {
      // The selected category was deleted: fall back to All.
      if (full && wantScope !== 'all' && isUnknownScope(e) && scope.value === wantScope) {
        resetScope()
        return
      }
      if (token === loadSeq) loadError.value = trainingErrorText(e)
      throw e
    }
  }

  function isUnknownScope(e: unknown): boolean {
    return e instanceof ApiError && (e.detail as { code?: unknown } | null)?.code === 'unknown_scope'
  }

  /** Back to All (the selected category no longer exists) and reload. */
  function resetScope(): void {
    if (scope.value === 'all') return
    scope.value = 'all'
    quietly(load(), 'load the status')
  }

  /** `categories_changed`: drop a scope whose category was deleted. */
  function checkScope(categoryIds: Iterable<number | string>): void {
    if (scope.value === 'all') return
    for (const id of categoryIds) if (String(id) === scope.value) return
    resetScope()
  }

  async function loadAdapters(): Promise<void> {
    adapters.value = await listWriterAdapters()
  }

  async function loadScorecards(): Promise<void> {
    const data = await fetchScorecards()
    scorecards.value = data
    scorecardRun.value = data.running
  }

  async function loadProfiles(): Promise<TrainingProfiles> {
    const data = await fetchTrainingProfiles()
    profiles.value = data
    return data
  }

  async function loadBriefs(): Promise<void> {
    briefs.value = await fetchBriefsStatus()
  }

  function quietly(p: Promise<unknown>, what: string): void {
    p.catch((e) => console.warn(`Training: could not ${what}`, e instanceof Error ? e.message : e))
  }

  function refreshAll(): void {
    quietly(load(), 'load the status')
    quietly(loadAdapters(), 'load the adapters')
    quietly(loadScorecards(), 'load the scorecards')
    if (windowOpen.value) quietly(loadProfiles(), 'load the training profiles')
  }

  function setScope(next: string): void {
    if (scope.value === next) return
    scope.value = next
    quietly(load(), 'load the status')
  }

  function openWindow(): void {
    windowOpen.value = true
    refreshAll()
    quietly(loadBriefs(), 'load the briefs status')
  }

  function closeWindow(): void {
    windowOpen.value = false
  }

  function noteProgress(runId: number, p: TrainingProgress): void {
    if (lossRunId.value !== runId) {
      lossRunId.value = runId
      lossPoints.value = []
    }
    if (typeof p.loss === 'number' && Number.isFinite(p.loss)) {
      const next = [...lossPoints.value, p.loss]
      lossPoints.value = next.length > MAX_LOSS_POINTS ? next.slice(-MAX_LOSS_POINTS) : next
    }
  }

  function endRun(kind: 'completed' | 'failed' | 'cancelled' | 'paused', text: string): void {
    if (status.value) status.value = { ...status.value, state: 'idle', run: null, gpu_locked: false }
    lastOutcome.value = { kind, text }
    refreshAll()
  }

  // run_progress of an unseen run: one debounced load per run id.
  let unseenRunId: number | null = null
  let unseenTimer: ReturnType<typeof setTimeout> | null = null
  function loadForUnseenRun(runId: number): void {
    if (!Number.isFinite(runId) || unseenRunId === runId) return
    unseenRunId = runId
    if (unseenTimer !== null) clearTimeout(unseenTimer)
    unseenTimer = setTimeout(() => {
      unseenTimer = null
      quietly(load(), 'load the status')
    }, 300)
  }

  // A rating burst: one results fetch per quiet half second. Also called by
  // the T2I / I2V dialogs, whose Writer menus show the scorecard rates.
  let scorecardTimer: ReturnType<typeof setTimeout> | null = null
  function scheduleScorecards(): void {
    if (scorecardTimer !== null) clearTimeout(scorecardTimer)
    scorecardTimer = setTimeout(() => {
      scorecardTimer = null
      quietly(loadScorecards(), 'load the scorecards')
    }, 500)
  }

  /** The always-on `training` channel bridge (App.vue). */
  function handleEvent(event: string, data: Record<string, unknown>): void {
    const runId = Number(data.run_id)
    switch (event) {
      case 'run_started': {
        lastOutcome.value = null
        const started = {
          run_id: runId,
          adapter_id: Number(data.adapter_id),
          name: String(data.name ?? ''),
          scope: String(data.scope ?? 'all'),
          resumed: data.resumed === true,
          state: 'running',
          progress: {},
        }
        if (lossRunId.value !== runId) {
          lossRunId.value = runId
          lossPoints.value = []
        }
        if (status.value) status.value = { ...status.value, state: 'running', gpu_locked: true, run: started }
        refreshAll()
        break
      }
      case 'run_progress': {
        const { run_id: _r, adapter_id: _a, ...progress } = data
        void _r
        void _a
        noteProgress(runId, progress as TrainingProgress)
        const r = status.value?.run
        if (r && r.run_id === runId) {
          status.value = { ...status.value!, run: { ...r, progress: progress as TrainingProgress } }
        } else {
          // Progress of a run this tab has not seen start (opened mid-run):
          // one load per run, not one per event.
          loadForUnseenRun(runId)
        }
        break
      }
      case 'run_paused':
        endRun('paused', 'Paused — resume continues from the last checkpoint.')
        break
      case 'run_completed':
        endRun('completed', 'Training finished: the new adapter is listed as untested.')
        break
      case 'run_failed':
        endRun('failed', `Training failed: ${String(data.error ?? 'unknown error')}`)
        break
      case 'run_cancelled':
        // Also sent when a paused / failed run is discarded (e.g. with its
        // adapter): only the run this tab shows as active "stopped".
        if (status.value?.run && status.value.run.run_id === runId) {
          endRun('cancelled', 'Training cancelled; its checkpoints were deleted.')
        } else {
          refreshAll()
        }
        break
      case 'adapters_changed':
        quietly(loadAdapters(), 'load the adapters')
        quietly(loadScorecards(), 'load the scorecards')
        break
      case 'scorecard_judged':
        // A scorecard image was graded: "n/30 judged" and the pass % move.
        if (windowOpen.value) scheduleScorecards()
        break
      case 'scorecard_started':
        scorecardOutcome.value = null
        scorecardRun.value = {
          adapter_id: Number(data.adapter_id),
          scorecard_id: String(data.scorecard_id ?? ''),
          phase: 'prompting',
          done: 0,
          total: Number(data.todo ?? data.total ?? 0),
        }
        break
      case 'scorecard_progress':
        scorecardRun.value = {
          adapter_id: Number(data.adapter_id),
          scorecard_id: String(data.scorecard_id ?? ''),
          phase: data.phase === 'rendering' ? 'rendering' : 'prompting',
          done: Number(data.done ?? 0),
          total: Number(data.total ?? 0),
        }
        break
      case 'scorecard_complete': {
        scorecardRun.value = null
        const missing = Number(data.missing ?? 0)
        scorecardOutcome.value =
          missing > 0
            ? `Scorecard rendered ${String(data.done)} of ${String(data.total)} briefs; ${missing} did not render — Resume renders them.`
            : 'Scorecard rendered: grade its images (✓ pass or ✗ fail) to score the adapter.'
        quietly(loadScorecards(), 'load the scorecards')
        break
      }
      case 'scorecard_failed':
        scorecardRun.value = null
        scorecardOutcome.value = `Scorecard failed: ${trainingErrorText(
          new ApiError(409, { code: data.code }, String(data.error ?? data.code ?? 'error')),
        )}`
        quietly(loadScorecards(), 'load the scorecards')
        break
      case 'scorecard_cancelled':
        scorecardRun.value = null
        scorecardOutcome.value = 'Scorecard cancelled; Resume renders the missing briefs.'
        quietly(loadScorecards(), 'load the scorecards')
        break
      case 'gate_changed':
        if (windowOpen.value) quietly(load(), 'load the status')
        break
      case 'briefs_progress':
        briefs.value = {
          running: true,
          failed: briefs.value?.failed ?? 0,
          result: null,
          done: Number(data.done ?? 0),
          total: Number(data.total ?? 0),
        }
        break
      case 'briefs_done':
      case 'briefs_cancelled':
      case 'briefs_error':
        briefs.value = {
          running: false,
          done: Number(data.done ?? briefs.value?.done ?? 0),
          total: Number(data.total ?? briefs.value?.total ?? 0),
          failed: Number(data.failed ?? 0),
          result: event === 'briefs_done' ? 'done' : event === 'briefs_cancelled' ? 'cancelled' : `error:${String(data.code ?? '')}`,
        }
        // New briefs are new hindsight examples.
        if (event === 'briefs_done' && windowOpen.value) quietly(load(), 'load the status')
        break
    }
  }

  // ---- actions (throw ApiError; the window renders trainingErrorText) ----

  async function start(body: StartTrainingRequest): Promise<void> {
    lastOutcome.value = null
    const r = await startTraining(body)
    if (status.value) status.value = { ...status.value, state: r.state, gpu_locked: r.gpu_locked, run: r.run }
    refreshAll()
  }

  async function pause(): Promise<void> {
    const r = await pauseTraining()
    if (status.value) status.value = { ...status.value, state: r.state, gpu_locked: r.gpu_locked, run: r.run }
  }

  async function resume(runId: number): Promise<void> {
    lastOutcome.value = null
    const r = await resumeTraining(runId)
    if (status.value) status.value = { ...status.value, state: r.state, gpu_locked: r.gpu_locked, run: r.run }
    refreshAll()
  }

  async function cancel(runId?: number): Promise<void> {
    const r = await cancelTraining(runId)
    if (status.value) status.value = { ...status.value, state: r.state, gpu_locked: r.gpu_locked, run: r.run }
    if (runId !== undefined) refreshAll()
  }

  async function rename(id: number, name: string): Promise<void> {
    await renameWriterAdapter(id, name)
    await loadAdapters()
  }

  async function remove(id: number): Promise<void> {
    await deleteWriterAdapter(id)
    refreshAll()
  }

  async function renderScorecard(adapterId: number, fresh = false): Promise<void> {
    scorecardOutcome.value = null
    await renderScorecardApi(adapterId, { fresh })
  }

  async function cancelScorecard(): Promise<void> {
    await cancelScorecardApi()
  }

  async function startBriefs(): Promise<void> {
    await runBriefs()
    briefs.value = { running: true, done: 0, total: 0, failed: 0, result: null }
  }

  async function stopBriefs(): Promise<void> {
    await cancelBriefs()
  }

  return {
    status,
    adapters,
    briefs,
    windowOpen,
    scope,
    lossPoints,
    lastOutcome,
    loadError,
    scorecards,
    scorecardRun,
    scorecardOutcome,
    profiles,
    profileStats,
    loadProfiles,
    run,
    runActive,
    locked,
    resumable,
    pct,
    chipText,
    load,
    loadAdapters,
    loadBriefs,
    loadScorecards,
    scheduleScorecards,
    renderScorecard,
    cancelScorecard,
    refreshAll,
    setScope,
    checkScope,
    openWindow,
    closeWindow,
    handleEvent,
    start,
    pause,
    resume,
    cancel,
    rename,
    remove,
    startBriefs,
    stopBriefs,
  }
})
