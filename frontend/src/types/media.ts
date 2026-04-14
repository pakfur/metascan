export interface LoRA {
  lora_name: string
  lora_weight: number
}

export interface Media {
  file_path: string
  file_name: string
  file_size: number
  width: number
  height: number
  format: string
  created_at: string | null
  modified_at: string | null
  metadata_source: string | null
  prompt: string | null
  negative_prompt: string | null
  model: string[]
  sampler: string | null
  scheduler: string | null
  steps: number | null
  cfg_scale: number | null
  seed: number | null
  frame_rate: number | null
  duration: number | null
  video_length: number | null
  tags: string[]
  loras: LoRA[]
  is_favorite: boolean
  is_video: boolean
  media_type: 'image' | 'video'
  playback_speed: number | null
  similarity_score?: number
}
