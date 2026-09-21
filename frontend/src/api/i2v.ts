import { get, post, del } from './client'
import type { I2vConfig, I2vLintReport, I2vVideo } from '../types/i2v'

export function generatePrompt(body: {
  source_path: string
  idea: string
  duration_s: number
}): Promise<{ prompt: string; warnings: string[] }> {
  return post('/i2v/prompt', body)
}

// Advisory lint of the prompt text plus the no-model rewrites it could
// apply (camera phrasing, speech form). Never changes anything itself.
export function lintI2vPrompt(body: {
  prompt: string
  duration_s: number
}): Promise<I2vLintReport> {
  return post('/i2v/lint', body)
}

export function generateVideo(body: {
  source_path: string
  prompt: string
  duration_s: number
  quality: 'fast' | 'quality'
  seed: number
  megapixels: number
  loras: { name: string; strength: number }[]
  idea?: string
  // High quality only; the server ignores it for Fast.
  steps?: number
}): Promise<{ job_id: number; warnings: string[] }> {
  return post('/i2v/generate', body)
}

export function listI2vVideos(sourcePath: string): Promise<I2vVideo[]> {
  return get(`/i2v/videos?source_path=${encodeURIComponent(sourcePath)}`)
}

export function deleteI2vVideo(id: number): Promise<{ status: string }> {
  return del(`/i2v/videos/${id}`)
}

export function fetchI2vConfig(): Promise<I2vConfig> {
  return get('/i2v/config')
}

// Where a clip generated right now would land for these (possibly
// unsaved) settings. Always resolves: problems come back as `error`.
export function previewI2vOutput(
  root: string,
  prefix: string,
): Promise<{ path: string | null; error: string | null; warnings: string[] }> {
  const q = `root=${encodeURIComponent(root)}&prefix=${encodeURIComponent(prefix)}`
  return get(`/i2v/output-preview?${q}`)
}
