import { del, get, post } from './client'

export type TargetModel = 'sd' | 'pony' | 'flux1' | 'flux2' | 'zimage' | 'chroma' | 'qwen'
export type Architecture = 't2i'
export type ExtraOption =
  | 'excludeStaticAttributes'
  | 'includeLighting'
  | 'includeCameraAngle'
  | 'includeWatermark'
  | 'includeArtifacts'
  | 'includeTechnicalDetails'
  | 'keepPG'
  | 'excludeResolution'
  | 'includeAestheticQuality'
  | 'includeComposition'
  | 'excludeText'
  | 'includeDOF'
  | 'includeLightSource'
  | 'noAmbiguity'
  | 'includeSafety'
  | 'includeUncensored'
export type CaptionLength = 'Short' | 'Medium' | 'Long' | 'Descriptive (Longest)'
export type PromptMode = 'generate' | 'transform' | 'clean'

export const TARGET_MODEL_LABELS: Record<TargetModel, string> = {
  'sd': 'Stable Diffusion',
  'pony': 'Pony (SDXL)',
  'flux1': 'Flux 1',
  'flux2': 'Flux 2',
  'zimage': 'Z-Image',
  'chroma': 'Chroma',
  'qwen': 'Qwen Image',
}

export const TARGET_MODEL_ORDER: TargetModel[] = [
  'sd', 'pony', 'flux1', 'flux2', 'zimage', 'chroma', 'qwen',
]

export interface ExtraOptionDef {
  key: ExtraOption
  short: string
  full: string
}

// Order matches the reference panel; the UI renders this list verbatim.
export const EXTRA_OPTIONS: ExtraOptionDef[] = [
  { key: 'excludeStaticAttributes', short: 'Exclude static attributes', full: 'Do NOT include information about people/characters that cannot be changed (like ethnicity, gender, etc), but do still include changeable attributes (like hair style).' },
  { key: 'includeLighting', short: 'Lighting', full: 'Include information about lighting.' },
  { key: 'includeCameraAngle', short: 'Camera angle', full: 'Include information about camera angle.' },
  { key: 'includeWatermark', short: 'Watermark detection', full: 'Include information about whether there is a watermark or not.' },
  { key: 'includeArtifacts', short: 'JPEG artifacts', full: 'Include information about whether there are JPEG artifacts or not.' },
  { key: 'includeTechnicalDetails', short: 'Camera / tech details', full: 'If it is a photo you MUST include information about what camera was likely used and details such as aperture, shutter speed, ISO, etc.' },
  { key: 'keepPG', short: 'Keep PG (no NSFW)', full: 'Do NOT include anything sexual; keep it PG.' },
  { key: 'excludeResolution', short: 'Exclude resolution', full: "Do NOT mention the image's resolution." },
  { key: 'includeAestheticQuality', short: 'Aesthetic quality', full: 'You MUST include information about the subjective aesthetic quality of the image from low to very high.' },
  { key: 'includeComposition', short: 'Composition style', full: "Include information on the image's composition style, such as leading lines, rule of thirds, or symmetry." },
  { key: 'excludeText', short: 'Exclude text / OCR', full: 'Do NOT mention any text that is in the image.' },
  { key: 'includeDOF', short: 'Depth of field', full: 'Specify the depth of field and whether the background is in focus or blurred.' },
  { key: 'includeLightSource', short: 'Light sources', full: 'If applicable, mention the likely use of artificial or natural lighting sources.' },
  { key: 'noAmbiguity', short: 'No ambiguous language', full: 'Do NOT use any ambiguous language.' },
  { key: 'includeSafety', short: 'SFW / NSFW rating', full: 'Include whether the image is sfw, suggestive, or nsfw.' },
  { key: 'includeUncensored', short: 'Uncensored / Adult Detail', full: 'Describe all adult/NSFW content in explicit detail, including positions, looks, clothing/nudity, sexual activity, and provocative elements.' },
]

// Mutually-exclusive option pairs. When one is checked, the other is unchecked.
export const MUTEX_PAIRS: ReadonlyArray<readonly [ExtraOption, ExtraOption]> = [
  ['keepPG', 'includeUncensored'],
]

export const CAPTION_LENGTH_ORDER: CaptionLength[] = [
  'Short', 'Medium', 'Long', 'Descriptive (Longest)',
]

const _ALL_LENGTHS: CaptionLength[] = [...CAPTION_LENGTH_ORDER]
const _TAG_LENGTHS: CaptionLength[] = ['Short', 'Medium', 'Long']

export interface TargetPreset {
  id: TargetModel
  label: string
  prefix: string
  suffix: string
  allowedLengths: CaptionLength[]
}

export const TARGET_PRESETS: Record<TargetModel, TargetPreset> = {
  sd: { id: 'sd', label: 'Stable Diffusion', prefix: '', suffix: ', high quality, masterwork', allowedLengths: _TAG_LENGTHS },
  pony: { id: 'pony', label: 'Pony (SDXL)', prefix: 'score_9, score_8_up, score_7_up, ', suffix: ', rating_safe', allowedLengths: _TAG_LENGTHS },
  flux1: { id: 'flux1', label: 'Flux 1', prefix: '', suffix: '', allowedLengths: _ALL_LENGTHS },
  flux2: { id: 'flux2', label: 'Flux 2', prefix: '', suffix: '', allowedLengths: _ALL_LENGTHS },
  zimage: { id: 'zimage', label: 'Z-Image', prefix: '', suffix: '', allowedLengths: _ALL_LENGTHS },
  chroma: { id: 'chroma', label: 'Chroma', prefix: '', suffix: '', allowedLengths: _ALL_LENGTHS },
  qwen: { id: 'qwen', label: 'Qwen Image', prefix: '', suffix: '', allowedLengths: _ALL_LENGTHS },
}

export interface GenerateBody {
  file_path: string
  target_model: TargetModel
  architecture: Architecture
  extras: ExtraOption[]
  caption_length: CaptionLength
  temperature: number
  max_tokens: number
}

export interface TransformBody {
  source_prompt: string
  target_model: TargetModel
  architecture: Architecture
  extras: ExtraOption[]
  caption_length: CaptionLength
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
