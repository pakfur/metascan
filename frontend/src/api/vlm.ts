import { del, get, post } from './client'

export type VlmState =
  | 'idle' | 'spawning' | 'loading' | 'ready' | 'error' | 'stopped'

export interface VlmStatus {
  state: VlmState
  model_id: string | null
  base_url: string | null
  progress: Record<string, unknown>
  error: string | null
  /** Roles the emitting client serves (WS payloads only). */
  roles?: VlmRole[]
}

export type VlmRole = 'writer' | 'general'

export interface VlmRoleRow {
  /** Configured model for the role; null for general on CPU-only machines. */
  model_id: string | null
  state: VlmState | null
  loaded_model_id: string | null
}

export interface VlmRolesSnapshot {
  writer: VlmRoleRow
  general: VlmRoleRow
  shared: boolean
  co_resident: boolean
  writer_falls_back: boolean
  writer_missing: boolean
  notes: string[]
}

export interface VlmRetagJob {
  job_id: string
  total: number
}

export interface RetagBody {
  scope: 'paths' | 'all_clip'
  paths?: string[]
  force?: boolean
}

export function fetchVlmStatus(): Promise<VlmStatus> {
  return get<VlmStatus>('/vlm/status')
}

export function tagOne(path: string): Promise<{ tags: string[] }> {
  return post<{ tags: string[] }>('/vlm/tag', { path })
}

export function startRetag(body: RetagBody): Promise<VlmRetagJob> {
  return post<VlmRetagJob>('/vlm/retag', body)
}

export function cancelRetag(jobId: string): Promise<{ status: string }> {
  return del<{ status: string }>(`/vlm/retag/${encodeURIComponent(jobId)}`)
}

export function setActiveVlm(modelId: string): Promise<VlmStatus> {
  return post<VlmStatus>('/vlm/active', { model_id: modelId })
}

export function unloadVlm(modelId: string): Promise<VlmStatus> {
  return post<VlmStatus>('/vlm/unload', { model_id: modelId })
}

export function fetchVlmRoles(): Promise<VlmRolesSnapshot> {
  return get<VlmRolesSnapshot>('/vlm/roles')
}

export function setVlmRole(role: VlmRole, modelId: string): Promise<VlmRolesSnapshot> {
  return post<VlmRolesSnapshot>('/vlm/roles', { role, model_id: modelId })
}
