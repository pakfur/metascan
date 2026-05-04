import { del, get, post } from './client'

export type TargetModel = 'sdxl' | 'flux-chroma' | 'qwen-t2i' | 'pony'
export type Architecture = 't2i'
export type StyleEnhancement =
  | 'anime'
  | 'photorealistic'
  | 'cinematic'
  | 'cartoon'
  | 'watercolor'
  | 'oil-painting'
  | 'comic'
  | 'hyperdetailed'
  | 'minimalist'
  | 'moody-lighting'
export type PromptMode = 'generate' | 'transform' | 'clean'

export const TARGET_MODEL_LABELS: Record<TargetModel, string> = {
  'sdxl': 'SDXL',
  'flux-chroma': 'Flux / Chroma',
  'qwen-t2i': 'Qwen-Image',
  'pony': 'Pony / Illustrious',
}

export const STYLE_LABELS: Record<StyleEnhancement, string> = {
  'anime': 'Anime',
  'photorealistic': 'Photorealistic',
  'cinematic': 'Cinematic',
  'cartoon': 'Cartoon',
  'watercolor': 'Watercolor',
  'oil-painting': 'Oil painting',
  'comic': 'Comic',
  'hyperdetailed': 'Hyperdetailed',
  'minimalist': 'Minimalist',
  'moody-lighting': 'Moody lighting',
}

export interface GenerateBody {
  file_path: string
  target_model: TargetModel
  architecture: Architecture
  styles: StyleEnhancement[]
  temperature: number
  max_tokens: number
}

export interface TransformBody {
  source_prompt: string
  target_model: TargetModel
  architecture: Architecture
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
  styles: StyleEnhancement[]
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
