import type { Media } from '../types/media'
import { get, patch, del } from './client'

export function fetchAllMedia(sort = 'date_added', favoritesOnly = false): Promise<Media[]> {
  const params = new URLSearchParams({ sort })
  if (favoritesOnly) params.set('favorites_only', 'true')
  return get<Media[]>(`/media?${params}`)
}

// Fetch the full detail record for a single file — includes AI-generation
// fields (prompt, model, tags, loras, etc.) that the list endpoint omits.
// Callers should NOT cache the result; fetch again on each selection so the
// panel always reflects the latest DB state.
export function fetchMediaDetails(filePath: string): Promise<Media> {
  return get<Media>(`/media/${encodeURIComponent(filePath)}`)
}

export function deleteMedia(filePath: string): Promise<{ status: string }> {
  return del<{ status: string }>(`/media/${encodeURIComponent(filePath)}`)
}

export function updateMedia(filePath: string, updates: Partial<Pick<Media, 'is_favorite' | 'playback_speed'>>): Promise<Media> {
  return patch<Media>(`/media/${encodeURIComponent(filePath)}`, updates)
}
