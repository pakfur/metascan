import { get, post, del } from './client'
import type { I2vConfig, I2vVideo } from '../types/i2v'

export function generatePrompt(body: {
  source_path: string
  idea: string
  duration_s: number
}): Promise<{ prompt: string; warnings: string[] }> {
  return post('/i2v/prompt', body)
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
