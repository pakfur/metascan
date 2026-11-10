import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import type { Media } from '../types/media'
import type {
  UpscaleCandidate,
  UpscaleConfig,
  UpscaleGeneration,
  UpscaleRecord,
  UpscaleRun,
  UpscaleRunBody,
} from '../types/upscale'
import {
  acceptCandidate,
  cancelUpscaleRun,
  discardCandidate,
  fetchUpscaleConfig,
  fetchUpscaleState,
  listUpscaleRuns,
  revertUpscale,
  startUpscaleRun,
} from '../api/upscale'
import { useJobTracker } from '../composables/useJobTracker'

// POSIX/native tolerance: job rows store POSIX, Media paths are native.
function norm(p: string): string {
  return p.replace(/\\/g, '/')
}

export const useUpscaleStore = defineStore('upscale', () => {
  // The open item's path. An accept or revert that changes the extension
  // moves it, so it is followed rather than read from the Media row.
  const sourcePath = ref<string | null>(null)
  const config = ref<UpscaleConfig | null>(null)
  const record = ref<UpscaleRecord | null>(null)
  const candidates = ref<UpscaleCandidate[]>([])
  // What the item was generated with: the dialog's pre-fill.
  const generation = ref<UpscaleGeneration | null>(null)
  const runs = ref<UpscaleRun[]>([])
  const note = ref<string | null>(null)

  const tracker = useJobTracker({
    match: (job) =>
      sourcePath.value !== null &&
      job.upscale_source_path != null &&
      norm(job.upscale_source_path) === norm(sourcePath.value),
  })

  const sourceRuns = computed(() =>
    runs.value.filter(
      (r) => sourcePath.value !== null && norm(r.source_path) === norm(sourcePath.value),
    ),
  )

  let stateSeq = 0
  async function refreshState(): Promise<void> {
    const path = sourcePath.value
    if (!path) return
    const seq = ++stateSeq
    const state = await fetchUpscaleState(path)
    if (seq !== stateSeq) return // a newer refresh, or another item
    record.value = state.record
    candidates.value = state.candidates
    generation.value = state.generation ?? null
  }

  let runsSeq = 0
  async function refreshRuns(): Promise<void> {
    const seq = ++runsSeq
    const rows = await listUpscaleRuns()
    if (seq === runsSeq) runs.value = rows
  }

  async function open(media: Media): Promise<void> {
    sourcePath.value = media.file_path
    record.value = null
    candidates.value = []
    generation.value = null
    runs.value = []
    note.value = null
    tracker.reset()
    config.value = await fetchUpscaleConfig()
    await Promise.all([
      refreshState(),
      tracker.refresh(),
      refreshRuns().catch(() => {}),
    ])
  }

  function close(): void {
    sourcePath.value = null
    stateSeq += 1
    runsSeq += 1
    tracker.reset()
  }

  async function run(body: UpscaleRunBody): Promise<number | null> {
    note.value = null
    const started = await startUpscaleRun(body)
    await refreshRuns().catch(() => {})
    return started.next_seed
  }

  async function cancelAll(): Promise<void> {
    await Promise.all(sourceRuns.value.map((r) => cancelUpscaleRun(r.batch_id)))
    await tracker.cancelAll()
    await refreshRuns().catch(() => {})
  }

  async function accept(id: number): Promise<string> {
    const { file_path } = await acceptCandidate(id)
    sourcePath.value = file_path
    await refreshState()
    return file_path
  }

  async function revert(): Promise<string | null> {
    if (!sourcePath.value) return null
    const { file_path } = await revertUpscale(sourcePath.value)
    sourcePath.value = file_path
    await refreshState()
    return file_path
  }

  async function discard(id: number): Promise<void> {
    await discardCandidate(id)
    await refreshState()
  }

  function handleUpscaleEvent(event: string, data: Record<string, unknown>): void {
    const path = typeof data.source_path === 'string' ? data.source_path : null
    const ours = path !== null && sourcePath.value !== null && norm(path) === norm(sourcePath.value)
    if (event === 'media_swapped') {
      const from = String(data.path ?? '')
      if (sourcePath.value && norm(from) === norm(sourcePath.value)) {
        sourcePath.value = String(data.new_path ?? from)
        void refreshState().catch(() => {})
      }
      return
    }
    if (event === 'candidates_changed') {
      if (ours) void refreshState().catch(() => {})
      return
    }
    if (event === 'no_output') {
      if (ours) note.value = 'A job finished without a file Metascan can read.'
      return
    }
    if (event.startsWith('batch_')) {
      if (ours && event === 'batch_failed') {
        note.value = typeof data.error === 'string' ? data.error : 'The run failed.'
      }
      void refreshRuns().catch(() => {})
    }
  }

  return {
    sourcePath,
    config,
    record,
    candidates,
    generation,
    runs,
    sourceRuns,
    note,
    jobs: tracker.jobs,
    activeJobIds: tracker.activeJobIds,
    cancelJob: tracker.cancel,
    dismissJob: tracker.dismiss,
    handleComfyEvent: tracker.handleComfyEvent,
    open,
    close,
    run,
    cancelAll,
    accept,
    revert,
    discard,
    refreshState,
    handleUpscaleEvent,
  }
})
