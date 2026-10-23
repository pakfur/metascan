import { get, post } from './client'
import type {
  BriefsStatus,
  DatasetPreview,
  StartTrainingRequest,
  TrainingStatus,
} from '../types/training'

// Prompt-writer LoRA training. Refusals are ApiError with `detail.code`
// (409 already_training / not_ready / gate_unavailable / gate_checking /
// gpu_busy / run_discarded / worker_unverifiable / worker_alive …, 422 for
// bad input); see trainingErrorText in stores/training.ts.

/** Status, dataset summary of `scope`, gate and presets. Never blocks on the gate probe. */
export function fetchTrainingStatus(scope = 'all', recheck = false): Promise<TrainingStatus> {
  const q = new URLSearchParams({ scope })
  if (recheck) q.set('recheck', '1')
  return get<TrainingStatus>(`/writers/training?${q.toString()}`)
}

export type TrainingActionResult = Pick<TrainingStatus, 'state' | 'gpu_locked' | 'run'> & { run_id?: number }

export function startTraining(body: StartTrainingRequest): Promise<TrainingActionResult> {
  return post<TrainingActionResult>('/writers/training/start', body)
}

export function pauseTraining(): Promise<TrainingActionResult> {
  return post<TrainingActionResult>('/writers/training/pause')
}

export function resumeTraining(runId: number): Promise<TrainingActionResult> {
  return post<TrainingActionResult>('/writers/training/resume', { run_id: runId })
}

/** Cancel the running run (no id) or a paused / failed one by id. */
export function cancelTraining(runId?: number): Promise<TrainingActionResult> {
  return post<TrainingActionResult>('/writers/training/cancel', runId === undefined ? {} : { run_id: runId })
}

/** Up to 10 preference pairs + 10 SFT examples (owner-only; never log them). */
export function fetchDatasetPreview(scope = 'all'): Promise<DatasetPreview> {
  return get<DatasetPreview>(`/writers/dataset/preview?scope=${encodeURIComponent(scope)}`)
}

/** Write hindsight briefs for favorites without one (202; progress on WS). */
export function runBriefs(): Promise<{ status: string }> {
  return post<{ status: string }>('/writers/briefs/run')
}

export function fetchBriefsStatus(): Promise<BriefsStatus> {
  return get<BriefsStatus>('/writers/briefs/status')
}

export function cancelBriefs(): Promise<{ status: string }> {
  return post<{ status: string }>('/writers/briefs/cancel')
}
