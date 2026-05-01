export interface FilterItem {
  key: string
  count: number
}

export type FilterData = Record<string, FilterItem[]>

export type ActiveFilters = Record<string, string[]>
