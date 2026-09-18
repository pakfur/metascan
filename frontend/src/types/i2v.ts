export interface I2vVideo {
  id: number
  source_path: string
  file_path: string
  prompt_used: string | null
  idea: string | null
  seed: number | null
  duration_s: number | null
  quality: string | null
  preset_id: number | null
  comfy_prompt_id: string | null
  created_at: string
  is_favorite: boolean
}

export interface I2vConfig {
  fast_preset_id: number | null
  quality_preset_id: number | null
  durations: number[]
  default_duration: number
  default_quality: 'fast' | 'quality'
}

export interface I2vJobChip {
  state: 'queued' | 'running' | 'failed'
  value?: number
  max?: number
  error?: string | null
}
