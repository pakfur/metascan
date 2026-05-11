import { del, get, post } from './client'

export type TargetModel = 'sd' | 'pony' | 'flux1' | 'flux2' | 'zimage' | 'chroma' | 'qwen'
export type Architecture = 't2i'

// Two-option vocabulary: tell Qwen3 to keep output SFW, or to describe
// explicit / anatomically-correct content. Mutually exclusive. Leave
// both off to let Qwen3 decide based on the image.
export type ExtraOption = 'includeUncensored' | 'includeSafety'

export type PromptMode = 'generate' | 'transform' | 'clean'

export const TARGET_MODEL_LABELS: Record<TargetModel, string> = {
  'sd': 'Stable Diffusion (SDXL)',
  'pony': 'Pony (SDXL)',
  'flux1': 'Flux 1',
  'flux2': 'Flux 2',
  'zimage': 'Z-Image Turbo',
  'chroma': 'Chroma',
  'qwen': 'Qwen-Image',
}

export const TARGET_MODEL_ORDER: TargetModel[] = [
  'sd', 'pony', 'flux1', 'flux2', 'zimage', 'chroma', 'qwen',
]

// Targets whose meta-prompt asks Qwen3 for a separate "Negative:" block.
// The UI shows a Negative textarea for these and hides it for the rest.
export const MODELS_WITH_NEGATIVE: ReadonlySet<TargetModel> = new Set([
  'sd', 'pony', 'chroma', 'qwen',
])

export interface ExtraOptionDef {
  key: ExtraOption
  short: string
  full: string
}

export const EXTRA_OPTIONS: ExtraOptionDef[] = [
  {
    key: 'includeSafety',
    short: 'Keep SFW',
    full: 'Tell Qwen3 to keep the description SFW: no nudity, no sexual acts, no exposed genitalia. Mutually exclusive with Uncensored.',
  },
  {
    key: 'includeUncensored',
    short: 'Uncensored / Adult Detail',
    full: 'Tell Qwen3 to describe nudity, anatomy, and sexual acts using explicit, anatomically-correct vocabulary. Mutually exclusive with Keep SFW.',
  },
]

// Mutually-exclusive pairs. When one is checked, the other is unchecked.
export const MUTEX_PAIRS: ReadonlyArray<readonly [ExtraOption, ExtraOption]> = [
  ['includeSafety', 'includeUncensored'],
]

export interface TargetPreset {
  id: TargetModel
  label: string
  prefix: string
  suffix: string
  hasNegative: boolean
}

export const TARGET_PRESETS: Record<TargetModel, TargetPreset> = {
  sd: { id: 'sd', label: 'Stable Diffusion (SDXL)', prefix: '', suffix: '', hasNegative: true },
  pony: { id: 'pony', label: 'Pony (SDXL)', prefix: '', suffix: '', hasNegative: true },
  flux1: { id: 'flux1', label: 'Flux 1', prefix: '', suffix: '', hasNegative: false },
  flux2: { id: 'flux2', label: 'Flux 2', prefix: '', suffix: '', hasNegative: false },
  zimage: { id: 'zimage', label: 'Z-Image Turbo', prefix: '', suffix: '', hasNegative: false },
  chroma: { id: 'chroma', label: 'Chroma', prefix: '', suffix: '', hasNegative: true },
  qwen: { id: 'qwen', label: 'Qwen-Image', prefix: '', suffix: '', hasNegative: true },
}

export interface GenerateBody {
  file_path: string
  target_model: TargetModel
  architecture: Architecture
  extras: ExtraOption[]
  // Per-element body overrides from the playground's editable table.
  // Index-aligned with the elements returned by getElements(); a null
  // (or missing tail) means "use the default for that row". Send an
  // empty array (or omit) to skip the override pass entirely.
  element_overrides?: (string | null)[]
  temperature: number
  max_tokens: number
}

export interface MetaElement {
  title: string
  default_body: string
}

export interface ElementsResponse {
  target_model: TargetModel
  elements: MetaElement[]
}

export interface TransformBody {
  source_prompt: string
  target_model: TargetModel
  architecture: Architecture
  extras: ExtraOption[]
  file_path?: string
  temperature: number
  max_tokens: number
}

export interface CleanBody {
  source_prompt: string
  temperature: number
  max_tokens: number
}

export interface GenerateResponse {
  prompt: string
  vlm_model_id: string
  elapsed_ms: number
  // Populated for SDXL / Pony / Chroma / Qwen-Image — the meta-prompts
  // for those targets ask Qwen3 to emit a separate Negative block.
  negative?: string | null
}

export interface SaveBody {
  file_path: string
  name: string
  prompt: string
  target_model: TargetModel
  architecture: Architecture
  styles: string[]
  temperature: number | null
  max_tokens: number | null
  source_prompt: string | null
  mode: PromptMode
  negative: string | null
  vlm_model_id: string | null
}

export interface SavedPrompt {
  id: number
  file_path: string
  name: string
  prompt: string
  negative: string | null
  target_model: string
  architecture: string
  styles: string[]
  temperature: number | null
  max_tokens: number | null
  source_prompt: string | null
  mode: PromptMode
  vlm_model_id: string | null
  created_at: string
  updated_at: string
}

export function generatePrompt(
  body: GenerateBody,
  signal?: AbortSignal,
): Promise<GenerateResponse> {
  return post<GenerateResponse>('/prompt/generate', body, signal)
}

export function transformPrompt(
  body: TransformBody,
  signal?: AbortSignal,
): Promise<GenerateResponse> {
  return post<GenerateResponse>('/prompt/transform', body, signal)
}

export function cleanPrompt(
  body: CleanBody,
  signal?: AbortSignal,
): Promise<GenerateResponse> {
  return post<GenerateResponse>('/prompt/clean', body, signal)
}

export function savePrompt(body: SaveBody): Promise<{ id: number }> {
  return post<{ id: number }>('/prompt/save', body)
}

export function listByImage(filePath: string): Promise<SavedPrompt[]> {
  const q = encodeURIComponent(filePath)
  return get<SavedPrompt[]>(`/prompt/by-image?file_path=${q}`)
}

export function deleteSavedPrompt(id: number): Promise<{ status: string }> {
  return del<{ status: string }>(`/prompt/${id}`)
}

export function getElements(target: TargetModel): Promise<ElementsResponse> {
  return get<ElementsResponse>(`/prompt/elements?target_model=${encodeURIComponent(target)}`)
}
