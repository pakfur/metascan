import type { FilterData, ActiveFilters } from '../types/filters'
import { get, post } from './client'

export function fetchFilterData(): Promise<FilterData> {
  return get<FilterData>('/filters')
}

export function applyFilters(filters: ActiveFilters): Promise<{ paths: string[] }> {
  return post<{ paths: string[] }>('/filters/apply', { filters })
}
