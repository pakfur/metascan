import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import type { Media } from '../types/media'
import type { I2vConfig, I2vFormState, I2vJobChip, I2vVideo } from '../types/i2v'
import { fetchI2vConfig, listI2vVideos, updateI2vFormState } from '../api/i2v'
import { cancelJob as apiCancelJob, listJobs } from '../api/comfy'
import { updateMedia } from '../api/media'

// POSIX/native tolerance: job rows store POSIX, Media paths are native.
function norm(p: string): string {
  return p.replace(/\\/g, '/')
}

export const useI2vStore = defineStore('i2v', () => {
  const source = ref<Media | null>(null)
  const videos = ref<I2vVideo[]>([])
  const jobs = ref<Map<number, I2vJobChip>>(new Map())
  const config = ref<I2vConfig | null>(null)

  async function open(media: Media): Promise<void> {
    source.value = media
    jobs.value = new Map()
    videos.value = []
    config.value = await fetchI2vConfig()
    await Promise.all([refreshVideos(), refreshActiveJobs()])
  }

  function close(): void {
    source.value = null
    videos.value = []
    jobs.value = new Map()
  }

  async function refreshVideos(): Promise<void> {
    if (!source.value) return
    videos.value = await listI2vVideos(source.value.file_path)
  }

  // Reload-survival: rebuild the in-flight set from the server, filtered
  // to this source image (the storyboard refreshActiveJobs pattern).
  async function refreshActiveJobs(): Promise<void> {
    if (!source.value) return
    const src = norm(source.value.file_path)
    const [queued, running] = await Promise.all([
      listJobs('queued', 1000),
      listJobs('running', 1000),
    ])
    const next = new Map<number, I2vJobChip>()
    for (const j of [...queued, ...running]) {
      if (j.i2v_source_path && norm(j.i2v_source_path) === src) {
        next.set(j.id, { state: j.state === 'running' ? 'running' : 'queued' })
      }
    }
    jobs.value = next
  }

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

  // i2v channel: strip refresh when clips for THIS source land.
  function handleI2vEvent(event: string, data: Record<string, unknown>): void {
    if (event !== 'i2v_videos_changed' || !source.value) return
    if (norm(String(data.source_path)) !== norm(source.value.file_path)) return
    void refreshVideos()
  }

  async function toggleFavorite(video: I2vVideo): Promise<void> {
    const nextVal = !video.is_favorite
    video.is_favorite = nextVal // optimistic
    try {
      await updateMedia(video.file_path, { is_favorite: nextVal })
    } catch (e) {
      video.is_favorite = !nextVal
      throw e
    }
  }

  // Autosave target for the dialog. Writes the clip's EDITABLE form state
  // only, then mirrors the server's merged result onto the local row so a
  // later click on that clip loads what was just saved.
  async function saveFormState(id: number, fields: Partial<I2vFormState>): Promise<void> {
    const res = await updateI2vFormState(id, fields)
    const row = videos.value.find((v) => v.id === id)
    if (row) row.form_state = res.form_state
  }

  return {
    source,
    videos,
    jobs,
    config,
    open,
    close,
    refreshVideos,
    refreshActiveJobs,
    trackJob,
    dismissJob,
    activeJobIds,
    cancelJob,
    cancelAllJobs,
    handleComfyEvent,
    handleI2vEvent,
    toggleFavorite,
    saveFormState,
  }
})
