import type { Media } from './media'

export interface UpscaleWorkflow {
  id: number
  name: string
  // What the graph can take; the dialog offers only these.
  has_seed: boolean
  has_prompt: boolean
  has_scale: boolean
}

export interface UpscaleConfig {
  original_root: string
  resolved_original_root: string
  default_image_preset_id: number | null
  default_video_preset_id: number | null
  max_count: number
  window: number
  scales: number[]
  default_scale: number
  image_workflows: UpscaleWorkflow[]
  video_workflows: UpscaleWorkflow[]
}

export interface UpscaleRecord {
  original_path: string
  original_name: string
  preset_id: number | null
  upscaled_at: string
}

export interface UpscaleCandidate {
  id: number
  source_path: string
  file_path: string
  kind: 'upscale_image' | 'upscale_video'
  preset_id: number | null
  preset_name: string | null
  seed: number | null
  prompt: string | null
  scale: number | null
  width: number | null
  height: number | null
  duration_s: number | null
  file_size: number | null
  render_s: number | null
  audio_lost: boolean
  job_id: number | null
  created_at: string
}

export interface UpscaleState {
  record: UpscaleRecord | null
  candidates: UpscaleCandidate[]
}

export interface UpscaleRun {
  batch_id: string
  source_path: string
  phase: string
  queued: number
  ended: number
  total: number
  error?: string
}

export interface UpscaleRunBody {
  source_path: string
  preset_id: number
  count: number
  seed: number | null
  seed_policy: 'fixed' | 'increment' | 'decrement' | 'random'
  prompt: string | null
  scale: number | null
}

export function candidateLabel(c: UpscaleCandidate): string {
  const size = c.width && c.height ? `${c.width}×${c.height}` : ''
  return [size, c.scale ? `×${c.scale}` : ''].filter(Boolean).join(' · ')
}

export function workflowsFor(config: UpscaleConfig, media: Media): UpscaleWorkflow[] {
  return media.is_video ? config.video_workflows : config.image_workflows
}

export function defaultWorkflowId(config: UpscaleConfig, media: Media): number | null {
  const list = workflowsFor(config, media)
  const wanted = media.is_video
    ? config.default_video_preset_id
    : config.default_image_preset_id
  return list.find((w) => w.id === wanted)?.id ?? list[0]?.id ?? null
}
