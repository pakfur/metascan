import type { FilterData, ActiveFilters } from '../types/filters'
import { get, post } from './client'

export function fetchFilterData(): Promise<FilterData> {
  return get<FilterData>('/filters')
}

export function applyFilters(filters: ActiveFilters): Promise<{ paths: string[] }> {
  return post<{ paths: string[] }>('/filters/apply', { filters })
}

export function fetchTagPaths(keys: string[]): Promise<Record<string, string[]>> {
  return post<Record<string, string[]>>('/filters/tag_paths', { keys })
}
