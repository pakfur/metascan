import { get, post, put } from './client'

export interface SimilaritySettings {
  clip_model: string
  device: string
  phash_threshold: number
  clip_threshold: number
  search_results_count: number
  video_keyframes: number
  compute_phash_during_scan: boolean
  embedding_stats: {
    total_media: number
    hashed: number
    embedded: number
    clip_model: string | null
  }
}

export function fetchSimilaritySettings(): Promise<SimilaritySettings> {
  return get<SimilaritySettings>('/similarity/settings')
}

export function updateSimilaritySettings(updates: Partial<SimilaritySettings>): Promise<Record<string, unknown>> {
  return put<Record<string, unknown>>('/similarity/settings', updates)
}

export function buildIndex(rebuild = false): Promise<{ status: string }> {
  return post<{ status: string }>(`/similarity/index/build?rebuild=${rebuild}`)
}

export function fetchEmbeddingStatus(): Promise<{
  total_media: number
  hashed: number
  embedded: number
  clip_model: string | null
}> {
  return get('/embeddings/status')
}
