import { computed, ref, watch, type Ref } from 'vue'
import type { JobChip } from '../types/jobs'

/**
 * The placeholder tile the user selected and what its job was submitted with
 * (GET /api/{t2i,i2v}/jobs/{id}), which the dialog shows read-only in its own
 * form. The selection ends by itself when the tile goes (the job finished,
 * was cancelled or was dismissed); the dialog then shows its form as it was.
 */
export function usePendingJob<T>(
  jobs: () => Map<number, JobChip>,
  fetchInfo: (jobId: number) => Promise<T>,
) {
  const selectedJobId = ref<number | null>(null)
  const info = ref<T | null>(null) as Ref<T | null>
  const loading = ref(false)
  const error = ref<string | null>(null)
  let seq = 0

  const chip = computed(() =>
    selectedJobId.value === null ? undefined : jobs().get(selectedJobId.value),
  )

  async function load(jobId: number): Promise<void> {
    const mine = ++seq
    loading.value = info.value === null
    error.value = null
    try {
      const row = await fetchInfo(jobId)
      if (mine !== seq) return
      info.value = row
    } catch (e) {
      if (mine !== seq) return
      error.value = `Couldn't load this job's details: ${e instanceof Error ? e.message : String(e)}`
    } finally {
      if (mine === seq) loading.value = false
    }
  }

  /** A click on a tile: select it, or deselect it when it is the one shown. */
  function toggle(jobId: number): void {
    if (selectedJobId.value === jobId) {
      clear()
      return
    }
    selectedJobId.value = jobId
    info.value = null
    void load(jobId)
  }

  function clear(): void {
    seq += 1
    selectedJobId.value = null
    info.value = null
    loading.value = false
    error.value = null
  }

  // The tile left: nothing to show. A state change (queued -> running) reads
  // the details again, since its start time moves.
  watch(
    () => chip.value?.state,
    (state, before) => {
      const id = selectedJobId.value
      if (id === null) return
      if (state === undefined) clear()
      else if (before !== undefined && state !== before) void load(id)
    },
  )

  return { selectedJobId, info, loading, error, chip, toggle, clear }
}
