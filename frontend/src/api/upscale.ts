import { get, post, patch, del } from './client'

export interface UpscaleTask {
  file_path: string
  scale_factor: number
  model_type: string
  face_enhance: boolean
  interpolate_frames: boolean
  fps_multiplier: number
  custom_fps: number | null
}

export interface UpscaleSubmit {
  tasks: UpscaleTask[]
  concurrent_workers: number
}

export function submitUpscale(body: UpscaleSubmit): Promise<{ task_ids: string[]; status: string }> {
  return post('/upscale', body)
}

export function fetchUpscaleQueue(): Promise<{ tasks: Record<string, unknown>[] }> {
  return get('/upscale/queue')
}

export function removeUpscaleTask(taskId: string): Promise<{ status: string }> {
  return del(`/upscale/${taskId}`)
}

export function cancelUpscaleTask(taskId: string): Promise<{ status: string }> {
  return patch(`/upscale/${taskId}`, { action: 'cancel' })
}

export function pauseAllUpscale(): Promise<{ status: string }> {
  return post('/upscale/pause-all')
}

export function resumeAllUpscale(): Promise<{ status: string }> {
  return post('/upscale/resume-all')
}

export function clearCompletedUpscale(): Promise<{ status: string }> {
  return post('/upscale/clear-completed')
}
