export interface LoRA {
  lora_name: string
  lora_weight: number
}

// `source` mirrors the `indices.source` column for tag rows: 'prompt'
// (parsed from generation metadata), 'clip' (CLIP zero-shot), 'vlm'
// (Qwen3-VL), or the merged forms 'both' (prompt+clip) and 'vlm+prompt'.
export type TagSource = 'prompt' | 'clip' | 'vlm' | 'both' | 'vlm+prompt'

export interface TagWithSource {
  name: string
  source: TagSource
}

export interface PhotoExposure {
  shutter_speed?: string | null
  aperture?: number | null
  iso?: number | null
  flash?: string | null
  focal_length?: number | null
  focal_length_35mm?: number | null
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

  // --- Photo summary fields (also returned by GET /api/media) ---
  camera_make?: string | null
  camera_model?: string | null
  datetime_original?: string | null
  gps_latitude?: number | null
  gps_longitude?: number | null
  orientation?: number | null

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
  tags?: TagWithSource[]
  loras?: LoRA[]
  lens_model?: string | null
  gps_altitude?: number | null
  photo_exposure?: PhotoExposure | null
}
