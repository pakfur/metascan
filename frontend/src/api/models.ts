import { del, get, post } from './client'

export type ModelStatus = 'available' | 'missing' | 'downloading' | 'error'
export type ModelGroup = 'Embedding' | 'Upscaling' | 'NLP'

export interface ModelRow {
  id: string
  group: ModelGroup
  name: string
  description: string
  status: ModelStatus
  size_bytes: number | null
  cache_path: string | null
  required_vram_mb: number | null
  embedding_dim?: number
  preload_at_startup: boolean
}

export interface ModelsStatusResponse {
  models: ModelRow[]
  hf_token_set: boolean
  current_clip_model: string
  current_clip_dim: number
}

export interface HardwareInfo {
  platform: string
  cpu_count: number | null
  cuda_available: boolean
  gpu_name: string | null
  vram_gb: number | null
}

export type InferenceState =
  | 'idle'
  | 'spawning'
  | 'loading'
  | 'ready'
  | 'error'
  | 'stopped'

export interface InferenceStatusPayload {
  state: InferenceState
  model_key: string | null
  device: string | null
  dim: number | null
  progress?: { stage?: string; percent?: number | null }
  error?: string | null
}

export function fetchModelsStatus(): Promise<ModelsStatusResponse> {
  return get<ModelsStatusResponse>('/models/status')
}

export function fetchHardware(): Promise<HardwareInfo> {
  return get<HardwareInfo>('/models/hardware')
}

export function fetchInferenceStatus(): Promise<InferenceStatusPayload> {
  return get<InferenceStatusPayload>('/models/inference-status')
}

export function startInference(): Promise<InferenceStatusPayload> {
  return post<InferenceStatusPayload>('/models/inference/start')
}

export function setPreload(id: string, enabled: boolean): Promise<{ preload_at_startup: string[] }> {
  return post('/models/preload', { id, enabled })
}

export function fetchHfTokenStatus(): Promise<{ set: boolean }> {
  return get('/models/hf-token')
}

export function setHfToken(token: string): Promise<{ set: boolean }> {
  return post('/models/hf-token', { token })
}

export function clearHfToken(): Promise<{ set: boolean }> {
  return del('/models/hf-token')
}

export function testHfToken(): Promise<{ ok: boolean; name?: string; error?: string; status?: number }> {
  return post('/models/hf-token/test')
}

export function downloadModel(id: string): Promise<{ status: string; id: string }> {
  return post('/models/download', { id })
}

export function deleteModel(id: string): Promise<{ ok: boolean }> {
  return del(`/models/${encodeURIComponent(id)}`)
}

export function rebuildEmbeddingIndex(): Promise<Record<string, unknown>> {
  return post('/models/rebuild-index')
}
