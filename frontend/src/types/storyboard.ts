export interface PanelImage {
  id: number
  panel_id: number
  file_path: string
  seed: number | null
  variant_index: number
  prompt_used: string | null
  preset_id: number | null
  comfy_prompt_id: string | null
  created_at: string
}

export interface DialogLine {
  subject_id: number | null
  voice: string | null
  delivery: string | null
  language: string
  text: string
}

export interface Beat {
  id: number
  panel_id: number
  sort_order: number
  duration_s: number
  action: string
  camera_motion: string | null
  camera_amplitude: string | null
  camera_speed: string | null
  is_cut: 0 | 1
  dialog: DialogLine[]
  sound: string | null
  created_at: string
  updated_at: string
}

export const CAMERA_MOTIONS = [
  'zoom_in', 'zoom_out', 'push_in', 'pull_out', 'pan_left', 'pan_right',
  'truck_left', 'truck_right', 'tilt_up', 'tilt_down', 'pedestal_up',
  'pedestal_down', 'arc', 'tracking', 'static', 'shake_slight',
  'shake_strong', 'pov', 'roll_cw', 'roll_ccw',
] as const
export const CAMERA_AMPLITUDES = ['small', 'large'] as const
export const CAMERA_SPEEDS = ['slow', 'fast'] as const
export const COMPOSE_STAGES = ['outline', 'scenes', 'shots', 'beats'] as const
export type ComposeStage = (typeof COMPOSE_STAGES)[number]

export interface Panel {
  id: number
  scene_id: number
  sort_order: number
  shot_size: string | null
  angle: string | null
  lens: string | null
  action: string
  subject_ids: number[]
  notes: string | null
  brief: string | null
  prompt: string | null
  prompt_locked: number // 0 | 1 from SQLite
  prompt_source: 'llm' | 'brief' | 'user' | null
  negative: string | null
  selected_image_id: number | null
  created_at: string
  updated_at: string
  duration_s: number
  images: PanelImage[]
  beats: Beat[]
}

// `db.get_panel` (used by the PATCH /panels/{id} and POST
// /panels/{id}/select routes) returns every panels-table column but never
// attaches `images` -- that assembly only happens in `get_storyboard_tree`.
// Callers of patchPanel/selectPanelImage must merge the images array back
// in from whatever they already have loaded. The same is true of `beats`
// (also assembled only in get_storyboard_tree) -- it's deliberately NOT
// added to the Omit<> below, and callers merging a PanelWithoutImages
// response onto a live Panel via Object.assign never delete either key
// since Object.assign only overwrites keys present on the source object.
export type PanelWithoutImages = Omit<Panel, 'images'>

export interface Scene {
  id: number
  storyboard_id: number
  sort_order: number
  name: string
  subtitle: string | null
  setting: string | null
  location: string | null
  time_of_day: string | null
  mood: string | null
  lighting: string | null
  notes: string | null
  panels: Panel[]
}

export interface Subject {
  id: number
  storyboard_id: number
  name: string
  description: string
  lora_name: string | null
  lora_strength: number | null
  reference_path: string | null
  sort_order: number
  voice: string | null
}

export interface StoryboardSummary {
  id: number
  name: string
  aspect_ratio: string
  target_model: string
  architecture: string
  preset_id: number | null
  base_seed: number
  batch_size: number
  folder_id: string | null
  created_at: string
  updated_at: string
}

export interface StoryboardTree extends StoryboardSummary {
  source_text: string | null
  style_block: string | null
  negative: string | null
  outline: string | null
  subjects: Subject[]
  scenes: Scene[]
}

export const SHOT_SIZES = ['ECU', 'CU', 'MCU', 'MS', 'MLS', 'WS', 'EWS'] as const
export const ANGLES = ['eye', 'low', 'high', 'overhead', 'dutch', 'ots', 'pov'] as const
export const LENSES = ['wide', 'normal', 'tele', 'macro'] as const
export const ASPECT_RATIOS = ['1:1', '4:3', '16:9', '2.39:1', '9:16'] as const
export const TARGET_MODELS = ['sd', 'pony', 'flux1', 'flux2', 'zimage', 'chroma', 'qwen'] as const

// ---- comfy ----

// `db.list_workflow_presets` (backing GET /comfy/presets) always selects
// id, name, kind, bindings, created_at, updated_at -- none of these are
// optional on the wire. `workflow_json` is deliberately omitted from the
// list query (graphs are large); it's only present on the single-preset
// row shape, which no current endpoint exposes.
export interface WorkflowPreset {
  id: number
  name: string
  kind: 't2i' | 'ref'
  bindings: string
  created_at: string
  updated_at: string
}

export type JobState = 'queued' | 'running' | 'done' | 'failed' | 'cancelled'

export interface GenerationJob {
  id: number
  preset_id: number
  panel_id: number | null
  state: JobState
  comfy_prompt_id: string | null
  params: string
  error: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
  output_dir: string | null
}
