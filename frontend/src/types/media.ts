export interface LoRA {
  lora_name: string
  lora_weight: number
}

// `Media` carries every field any component might display. The list
// endpoint returns only the "summary" fields (required, always present);
// the detail endpoint additionally populates the fields below marked
// optional. Summary fields are all backed by materialized SQL columns so
// the list endpoint can project them without touching the Media JSON blob.
export interface Media {
  // --- Summary fields (GET /api/media, PATCH) ---
  file_path: string
  is_favorite: boolean
  is_video: boolean
  playback_speed: number | null
  width: number
  height: number
  file_size: number
  frame_rate: number | null
  duration: number | null
  similarity_score?: number

  // --- Detail-only fields (GET /api/media/{path}) ---
  file_name?: string
  format?: string
  created_at?: string | null
  modified_at?: string | null
  media_type?: 'image' | 'video'
  metadata_source?: string | null
  prompt?: string | null
  negative_prompt?: string | null
  model?: string[]
  sampler?: string | null
  scheduler?: string | null
  steps?: number | null
  cfg_scale?: number | null
  seed?: number | null
  video_length?: number | null
  tags?: string[]
  loras?: LoRA[]
}
