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
  images: PanelImage[]
}

// `db.get_panel` (used by the PATCH /panels/{id} and POST
// /panels/{id}/select routes) returns every panels-table column but never
// attaches `images` -- that assembly only happens in `get_storyboard_tree`.
// Callers of patchPanel/selectPanelImage must merge the images array back
// in from whatever they already have loaded.
export type PanelWithoutImages = Omit<Panel, 'images'>

export interface Scene {
  id: number
  storyboard_id: number
  sort_order: number
  name: string
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
