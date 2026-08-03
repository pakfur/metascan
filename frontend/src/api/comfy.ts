import { get, post, del } from './client'
import type { GenerationJob, JobState, WorkflowPreset } from '../types/storyboard'

export function listPresets(): Promise<WorkflowPreset[]> {
  return get<WorkflowPreset[]>('/comfy/presets')
}

export function createPreset(body: {
  name: string
  kind: 't2i' | 'ref'
  workflow: Record<string, unknown>
}): Promise<{ id: number }> {
  return post<{ id: number }>('/comfy/presets', body)
}

export function deletePreset(id: number): Promise<{ status: string }> {
  return del<{ status: string }>(`/comfy/presets/${id}`)
}

export function listJobs(state?: JobState, limit = 100): Promise<GenerationJob[]> {
  const params = new URLSearchParams({ limit: String(limit) })
  if (state) params.set('state', state)
  return get<GenerationJob[]>(`/comfy/jobs?${params.toString()}`)
}

export function getJob(id: number): Promise<GenerationJob> {
  return get<GenerationJob>(`/comfy/jobs/${id}`)
}
