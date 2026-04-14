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

export interface SimilarityResult {
  file_path: string
  file_name: string
  file_size: number
  width: number
  height: number
  format: string
  is_favorite: boolean
  is_video: boolean
  media_type: 'image' | 'video'
  similarity_score: number
  [key: string]: unknown
}

export function searchSimilar(filePath: string, threshold = 0.7, maxResults = 100): Promise<SimilarityResult[]> {
  return post<SimilarityResult[]>('/similarity/search', {
    file_path: filePath,
    threshold,
    max_results: maxResults,
  })
}

export function contentSearch(query: string, maxResults = 100): Promise<SimilarityResult[]> {
  return post<SimilarityResult[]>('/similarity/content-search', {
    query,
    max_results: maxResults,
  })
}

export function findDuplicates(): Promise<{ groups: Record<string, unknown>[][]; total_groups: number }> {
  return post('/duplicates/find')
}

export function deleteDuplicates(filePaths: string[]): Promise<{ deleted: number }> {
  return post('/duplicates/delete', { file_paths: filePaths })
}
