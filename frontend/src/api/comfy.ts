import { get, post, del } from './client'
import type { GenerationJob, JobState, WorkflowPreset } from '../types/storyboard'

export function listPresets(): Promise<WorkflowPreset[]> {
  return get<WorkflowPreset[]>('/comfy/presets')
}

export function createPreset(body: {
  name: string
  kind: 't2i' | 'ref' | 'ref2v'
  workflow: Record<string, unknown>
  video_target?: string | null
  video_mode?: string | null
}): Promise<{ id: number; warnings: string[] }> {
  return post<{ id: number; warnings: string[] }>('/comfy/presets', body)
}

export interface ValidationFinding {
  level: 'error' | 'warning'
  code: string
  message: string
  node_id: string | null
}

export interface ValidationResult {
  ok: boolean
  findings: ValidationFinding[]
  fixes: { node_id: string; old_title: string | null; title: string; reason: string }[]
  fixed_workflow?: Record<string, unknown>
}

export function validatePreset(body: {
  kind: 't2i' | 'ref' | 'ref2v'
  workflow: Record<string, unknown>
  video_target?: string | null
  video_mode?: string | null
}): Promise<ValidationResult> {
  return post<ValidationResult>('/comfy/presets/validate', body)
}

export function deletePreset(id: number): Promise<{ status: string }> {
  return del<{ status: string }>(`/comfy/presets/${id}`)
}

// Lora filenames installed on the connected ComfyUI server. Empty when the
// server is unreachable -- pickers fall back to free-text entry.
export function listLoras(): Promise<string[]> {
  return get<string[]>('/comfy/loras')
}

export function listJobs(state?: JobState, limit = 100): Promise<GenerationJob[]> {
  const params = new URLSearchParams({ limit: String(limit) })
  if (state) params.set('state', state)
  return get<GenerationJob[]>(`/comfy/jobs?${params.toString()}`)
}

export function getJob(id: number): Promise<GenerationJob> {
  return get<GenerationJob>(`/comfy/jobs/${id}`)
}
