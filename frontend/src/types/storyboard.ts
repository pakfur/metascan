export interface BeatImage {
  id: number
  beat_id: number
  file_path: string
  seed: number | null
  variant_index: number
  prompt_used: string | null
  preset_id: number | null
  comfy_prompt_id: string | null
  created_at: string
}

// Rendered clips are panel-scoped (one clip covers the whole shot), unlike
// the per-beat keyframe images above.
export interface PanelVideo {
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
  shot_size: string | null
  angle: string | null
  lens: string | null
  subject_ids: number[]
  camera_motion: string | null
  camera_amplitude: string | null
  camera_speed: string | null
  is_cut: 0 | 1
  dialog: DialogLine[]
  sound: string | null
  kind: string | null
  brief: string | null
  prompt: string | null
  prompt_locked: number // 0 | 1 from SQLite
  prompt_source: 'llm' | 'brief' | 'user' | null
  selected_image_id: number | null
  images: BeatImage[]
  composition: string | null
  light_quality: string | null
  emotional_intent: string | null
  reveals: string | null
  movement_motivation: string | null
  created_at: string
  updated_at: string
}

export const COMPOSITIONS = [
  'thirds_left', 'thirds_right', 'centered', 'symmetrical',
  'negative_space', 'frame_in_frame', 'leading_lines', 'deep_staging',
] as const
export const LIGHT_QUALITIES = [
  'hard', 'soft', 'dappled', 'practical', 'window', 'firelight', 'ambient',
] as const
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

// One entry in a shot's stackable-lora list (panels.image_loras /
// panels.video_loras), injected into the preset's MS_LORA_STACK node.
export interface LoraEntry {
  name: string
  strength: number
}

export interface Panel {
  id: number
  scene_id: number
  sort_order: number
  action: string
  duration_s: number
  is_turn: 0 | 1
  subtext: string | null
  image_loras: LoraEntry[]
  video_loras: LoraEntry[]
  video_prompt: string | null
  video_prompt_locked: 0 | 1
  video_prompt_source: 'compiled' | 'user' | null
  video_prompt_warnings: string[]
  video_anchor: string | null
  video_compiled_anchor: string | null
  video_compiled_at: string | null
  created_at: string
  updated_at: string
  beats: Beat[]
  videos: PanelVideo[]
}

// `db.get_panel` (used by the PATCH /panels/{id} route) returns every
// panels-table column but never attaches `beats` -- that assembly only
// happens in `get_storyboard_tree`. Mirrors the BeatWithoutImages
// merge-caveat above: callers of patchPanel must merge the beats array
// back in from whatever they already have loaded.
export type PanelWithoutBeats = Omit<Panel, 'beats' | 'videos'>

export const VIDEO_ANCHORS = ['keeper', 'prev_last'] as const

// `db.get_beat` (used by the PATCH /beats/{id} and POST /beats/{id}/select
// routes) returns every beats-table column but never attaches `images` --
// that assembly only happens in `get_storyboard_tree`. Callers of
// patchBeat/selectBeatImage must merge the images array back in from
// whatever they already have loaded (Object.assign only overwrites keys
// present on the source object, so the local `images` array is left
// untouched by that merge).
export type BeatWithoutImages = Omit<Beat, 'images'>

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
  reference_path: string | null
  arc_beats: string[]
  charge_in: number | null
  charge_out: number | null
  function: string | null
  brief: string | null
  template_id: string | null
  composed_from: {
    stage: string
    template_id?: string | null
    outline_hash: string
    at: string
  } | null
  template_problems: string[]
  template_warnings: string[]
  outline_stale: boolean
  panels: Panel[]
}

// scenes.function — mirrors SCENE_FUNCTION_VALUES in
// metascan/core/storyboard_story.py.
export const SCENE_FUNCTIONS = [
  'negotiation', 'confession', 'confrontation', 'reveal', 'arrival',
  'physical_action', 'transit', 'contemplation',
] as const

export interface Subject {
  id: number
  storyboard_id: number
  name: string
  description: string
  lora_name: string | null
  lora_strength: number | null
  reference_path: string | null
  reference_path_2: string | null
  sort_order: number
  voice: string | null
  voice_ref_path: string | null
  // 1 when the first reference picture is a three-view character sheet —
  // the H3 compiler emits the sheet boilerplate (with the panel's actual
  // <Subject N>/<Picture N> labels) and appends the description after it.
  sheet_ref: 0 | 1
  // 1 when the reference picture is the shot's first-person camera vantage
  // (usually a partial torso view) — the H3 compiler merges the shot's
  // beats into a single [Shot 1] with in-shot timestamps and locks the
  // camera to that vantage (POV, static, eye height) for the whole clip.
  pov_ref: 0 | 1
  subject_type: 'character' | 'location' | 'prop'
}

export const SUBJECT_TYPES = ['character', 'location', 'prop'] as const

export interface StoryboardSummary {
  id: number
  name: string
  aspect_ratio: string
  target_model: string
  architecture: string
  preset_id: number | null
  base_seed: number
  batch_size: number
  pacing: string
  story_scale: string
  folder_id: string | null
  video_target: string | null
  video_mode: string | null
  video_preset_id: number | null
  notes: string | null
  video_output_dir: string | null
  video_name_template: string | null
  image_name_template: string | null
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
export const PACINGS = ['contemplative', 'standard', 'propulsive'] as const

// storyboards.story_scale — mirrors STORY_SCALES in
// metascan/core/storyboard_story.py (grammar caps + shot-count guidance
// + token budgets per compose run).
export const STORY_SCALES = ['short', 'standard', 'extended'] as const
export const STORY_SCALE_LABELS: Record<string, string> = {
  short: 'Short — tight arc, 1–4 shots per scene',
  standard: 'Standard — 2–4 shots per scene',
  extended: 'Extended — long middle, up to 12 shots per scene',
}

// ---- H3 video prompt compiler ----

// GET /storyboard/templates — summary shape for the shot-template picker.
export interface ShotTemplateSummary {
  id: string
  function: string
  roles: { id: string; screen_side: string; note: string }[]
  sections: { panel_index: number; duration_s: number; label: string; slot_count: number }[]
  slot_count: number
  duration_s: number
}

export const VIDEO_TARGETS = ['minimax'] as const
export const VIDEO_MODES = ['t2va', 'i2va', 'fl2va', 'ref2va'] as const
export const VIDEO_TARGET_CAPS: Record<string, number> = { minimax: 15 }
export const DEFAULT_SHOT_CAP = 15

// ---- comfy ----

// `db.list_workflow_presets` (backing GET /comfy/presets) always selects
// id, name, kind, bindings, created_at, updated_at -- none of these are
// optional on the wire. `workflow_json` is deliberately omitted from the
// list query (graphs are large); it's only present on the single-preset
// row shape, which no current endpoint exposes.
export interface WorkflowPreset {
  id: number
  name: string
  kind: 't2i' | 'ref' | 'ref2v'
  bindings: string
  // Optional dialect association (metascan/core/workflow_validation.py):
  // drives target-specific validation at registration and the mismatch
  // guard in generate_video. Null on legacy/untagged presets.
  video_target: string | null
  video_mode: string | null
  created_at: string
  updated_at: string
}

// A preset row's dialect tag for list/picker labels, e.g. " (minimax·ref2va)".
export function presetTag(p: WorkflowPreset): string {
  if (!p.video_target && !p.video_mode) return ''
  return ` (${[p.video_target, p.video_mode].filter(Boolean).join('·')})`
}

export type JobState = 'queued' | 'running' | 'done' | 'failed' | 'cancelled'

export interface GenerationJob {
  id: number
  preset_id: number
  panel_id: number | null
  beat_id: number | null
  state: JobState
  comfy_prompt_id: string | null
  params: string
  error: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
  output_dir: string | null
  i2v_source_path: string | null
}
