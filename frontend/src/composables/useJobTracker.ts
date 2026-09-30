import { computed, ref } from 'vue'
import type { GenerationJob } from '../types/storyboard'
import type { JobChip } from '../types/jobs'
import { cancelJob as apiCancelJob, getJob, listJobs } from '../api/comfy'

export interface JobTrackerOptions {
  /**
   * Which ComfyUI jobs this tracker owns, judged from the whole job row
   * (`t2i_batch_id`, `i2v_source_path`, ...). Only matching jobs ever
   * become chips.
   */
  match: (job: GenerationJob) => boolean
}

// Ids that were looked up and are not ours. Bounded so a long session cannot
// grow it without limit; dropping it only costs a few repeated lookups.
const REJECTED_CAP = 2000

interface Lookup {
  // A terminal job_update that landed while the lookup was in flight.
  terminal: { state: string; error: string | null } | null
}

function progressOf(chip: JobChip | undefined): { value: number; max: number } | undefined {
  return chip && chip.state === 'running' && chip.value !== undefined && chip.max !== undefined
    ? { value: chip.value, max: chip.max }
    : undefined
}

function chipFor(
  state: 'queued' | 'running',
  prev: JobChip | undefined,
  progress?: { value: number; max: number },
): JobChip {
  const chip: JobChip = { state }
  if (progress) {
    chip.value = progress.value
    chip.max = progress.max
  }
  if (prev?.cancelling) chip.cancelling = true
  return chip
}

/**
 * Tracks the ComfyUI jobs a feature cares about as strip-tile chips, from two
 * sources: `refresh()` (the server's queued + running lists, which is what
 * survives a page reload) and the comfy WebSocket channel via
 * `handleComfyEvent`. Generalised from stores/i2v.ts.
 *
 * Channel semantics: job_update failed keeps the chip until it is dismissed;
 * done / cancelled remove it; queued / running update it; job_progress
 * becomes value / max.
 *
 * A job created by a server-side runner (a T2I batch) is announced by a
 * `job_update` for an id this tracker has never seen, and that event carries
 * no batch id. So an unknown id is looked up once (GET /api/comfy/jobs/{id})
 * and adopted when `match` accepts the row. A non-matching answer is
 * remembered so a foreign job's later events do not repeat the request.
 *
 * useWebSocket needs a component setup context, so the tracker does not
 * subscribe: the owner forwards `comfy` channel events to `handleComfyEvent`.
 */
export function useJobTracker(options: JobTrackerOptions) {
  const { match } = options
  const jobs = ref<Map<number, JobChip>>(new Map())
  const lookups = new Map<number, Lookup>()
  let rejected = new Set<number>()
  // Bumped by reset(): lookups and refreshes that started earlier drop their result.
  let epoch = 0

  // Jobs that can still be cancelled (a failed tile is only dismissable).
  const activeJobIds = computed<number[]>(() =>
    [...jobs.value].filter(([, chip]) => chip.state !== 'failed').map(([id]) => id),
  )

  function setChip(jobId: number, chip: JobChip): void {
    const next = new Map(jobs.value)
    next.set(jobId, chip)
    jobs.value = next
  }

  function patchChip(jobId: number, fields: Partial<JobChip>): void {
    const chip = jobs.value.get(jobId)
    if (chip) setChip(jobId, { ...chip, ...fields })
  }

  function track(jobId: number): void {
    setChip(jobId, { state: 'queued' })
  }

  function dismiss(jobId: number): void {
    if (!jobs.value.has(jobId)) return
    const next = new Map(jobs.value)
    next.delete(jobId)
    jobs.value = next
  }

  /** Forget everything: chips, in-flight lookups and the not-ours cache. */
  function reset(): void {
    epoch += 1
    jobs.value = new Map()
    lookups.clear()
    rejected = new Set()
  }

  // Rebuilds the in-flight set from the server. Failed chips stay (they wait
  // for the user to dismiss them); queued / running chips the server no
  // longer lists are dropped; progress and a pending cancel carry over.
  async function refresh(): Promise<void> {
    const startEpoch = epoch
    const [queued, running] = await Promise.all([listJobs('queued', 1000), listJobs('running', 1000)])
    if (startEpoch !== epoch) return
    const prev = jobs.value
    const next = new Map<number, JobChip>()
    for (const [id, chip] of prev) {
      if (chip.state === 'failed') next.set(id, chip)
    }
    for (const row of [...queued, ...running]) {
      if (!match(row) || next.has(row.id)) continue
      const old = prev.get(row.id)
      next.set(
        row.id,
        row.state === 'running'
          ? chipFor('running', old, progressOf(old))
          : chipFor('queued', old),
      )
    }
    jobs.value = next
  }

  function remember(jobId: number): void {
    if (rejected.size >= REJECTED_CAP) rejected = new Set()
    rejected.add(jobId)
  }

  async function adopt(jobId: number): Promise<void> {
    if (rejected.has(jobId)) return
    const lookup: Lookup = { terminal: null }
    lookups.set(jobId, lookup)
    const startEpoch = epoch
    try {
      const row = await getJob(jobId)
      if (startEpoch !== epoch) return
      if (!match(row)) {
        remember(jobId)
        return
      }
      if (jobs.value.has(jobId)) return
      // What the events said while we waited is newer than the row we read.
      const outcome = lookup.terminal ?? { state: row.state as string, error: row.error }
      if (outcome.state === 'failed') {
        setChip(jobId, { state: 'failed', error: outcome.error ?? row.error ?? null })
      } else if (outcome.state === 'queued' || outcome.state === 'running') {
        setChip(jobId, { state: outcome.state })
      }
    } catch {
      // Not cached as a rejection: the next event for this id, or refresh(), retries.
    } finally {
      if (lookups.get(jobId) === lookup) lookups.delete(jobId)
    }
  }

  function onJobUpdate(jobId: number, state: string, error: string | null): void {
    const prev = jobs.value.get(jobId)
    if (prev) {
      if (state === 'failed') setChip(jobId, { state: 'failed', error })
      else if (state === 'done' || state === 'cancelled') dismiss(jobId)
      else setChip(jobId, chipFor(state === 'running' ? 'running' : 'queued', prev))
      return
    }
    const lookup = lookups.get(jobId)
    if (lookup) {
      if (state === 'failed' || state === 'done' || state === 'cancelled') {
        lookup.terminal = { state, error }
      }
      return
    }
    if (state === 'queued' || state === 'running' || state === 'failed') void adopt(jobId)
  }

  function onJobProgress(jobId: number, value: unknown, max: unknown): void {
    const prev = jobs.value.get(jobId)
    if (!prev || prev.state === 'failed') return
    if (typeof value === 'number' && typeof max === 'number' && Number.isFinite(value) && Number.isFinite(max)) {
      setChip(jobId, chipFor('running', prev, { value, max }))
    } else if (prev.state !== 'running') {
      setChip(jobId, chipFor('running', prev))
    }
  }

  /** The comfy channel: job_update and job_progress. Other events are ignored. */
  function handleComfyEvent(event: string, data: Record<string, unknown>): void {
    const jobId = Number(data.job_id)
    if (!Number.isFinite(jobId)) return
    if (event === 'job_update') {
      const error = typeof data.error === 'string' && data.error ? data.error : null
      onJobUpdate(jobId, String(data.state), error)
    } else if (event === 'job_progress') {
      onJobProgress(jobId, data.value, data.max)
    }
  }

  // Cancel one queued / running job. The chip normally leaves via the comfy
  // channel's job_update -> cancelled; it is also dropped here on success so a
  // missed WebSocket frame cannot strand it. On failure the chip stays with
  // its button re-armed, and the error propagates to the caller's toast.
  async function cancel(jobId: number): Promise<void> {
    const chip = jobs.value.get(jobId)
    if (!chip || chip.state === 'failed' || chip.cancelling) return
    patchChip(jobId, { cancelling: true })
    try {
      await apiCancelJob(jobId)
      dismiss(jobId)
    } catch (e) {
      patchChip(jobId, { cancelling: false })
      throw e
    }
  }

  // Cancel every in-flight job. Every job is attempted; the first failure is
  // rethrown after the rest have settled.
  async function cancelAll(): Promise<void> {
    const results = await Promise.allSettled(activeJobIds.value.map((id) => cancel(id)))
    const failed = results.find((r): r is PromiseRejectedResult => r.status === 'rejected')
    if (failed) throw failed.reason
  }

  return {
    jobs,
    activeJobIds,
    track,
    dismiss,
    cancel,
    cancelAll,
    refresh,
    handleComfyEvent,
    reset,
  }
}

export type JobTracker = ReturnType<typeof useJobTracker>
