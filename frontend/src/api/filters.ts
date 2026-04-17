import type { FilterData, ActiveFilters } from '../types/filters'
import { get, getWithPhases, post } from './client'
import type { FetchPhases } from './client'

export function fetchFilterData(): Promise<FilterData> {
  return get<FilterData>('/filters')
}

export function fetchFilterDataTimed(): Promise<{ data: FilterData; phases: FetchPhases }> {
  return getWithPhases<FilterData>('/filters')
}

export function applyFilters(filters: ActiveFilters): Promise<{ paths: string[] }> {
  return post<{ paths: string[] }>('/filters/apply', { filters })
}
