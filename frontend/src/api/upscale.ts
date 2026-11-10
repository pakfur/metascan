import { del, get, post } from './client'
import type {
  PromptFromImage,
  UpscaleConfig,
  UpscaleRun,
  UpscaleRunBody,
  UpscaleState,
} from '../types/upscale'

export function fetchUpscaleConfig(): Promise<UpscaleConfig> {
  return get<UpscaleConfig>('/upscale/config')
}

export function fetchUpscaleState(path: string): Promise<UpscaleState> {
  return get<UpscaleState>(`/upscale/state?path=${encodeURIComponent(path)}`)
}

export function startUpscaleRun(
  body: UpscaleRunBody,
): Promise<{ batch_id: string; total: number; next_seed: number | null }> {
  return post('/upscale/runs', body)
}

export function promptFromImage(
  sourcePath: string,
  model: string,
): Promise<PromptFromImage> {
  return post('/upscale/prompt-from-image', { source_path: sourcePath, model })
}

export function listUpscaleRuns(): Promise<UpscaleRun[]> {
  return get<UpscaleRun[]>('/upscale/runs')
}

export function cancelUpscaleRun(batchId: string): Promise<{ cancelled: boolean }> {
  return post(`/upscale/runs/${batchId}/cancel`)
}

export function acceptCandidate(id: number): Promise<{ file_path: string }> {
  return post(`/upscale/candidates/${id}/accept`)
}

export function discardCandidate(id: number): Promise<{ status: string }> {
  return del(`/upscale/candidates/${id}`)
}

export function revertUpscale(filePath: string): Promise<{ file_path: string }> {
  return post('/upscale/revert', { file_path: filePath })
}

export function setOriginalFolder(path: string): Promise<{ status: 'saved' | 'moving' }> {
  return post('/upscale/original-folder', { path })
}
