import { get, post, put, del } from './client'
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

// One preset WITH its graph — listPresets() omits it (the graphs are large).
export function getPreset(
  id: number,
): Promise<WorkflowPreset & { workflow: Record<string, unknown> }> {
  return get(`/comfy/presets/${id}`)
}

// Replaces a preset's workflow in place. Name, kind and the dialect tag are
// fixed at registration; the id is preserved, so config slots and job
// history keep pointing at it. Validation failures are the same structured
// 400 (`code: "validation_failed"`) createPreset gives.
export function updatePreset(
  id: number,
  workflow: Record<string, unknown>,
): Promise<{ id: number; warnings: string[] }> {
  return put<{ id: number; warnings: string[] }>(`/comfy/presets/${id}`, { workflow })
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

// Cancels a queued or running job. A running job is interrupted inside
// ComfyUI (by prompt id, so bystander prompts survive); the terminal
// state arrives on the `comfy` WS channel as job_update → cancelled.
export function cancelJob(id: number): Promise<{ status: string }> {
  return post<{ status: string }>(`/comfy/jobs/${id}/cancel`)
}
