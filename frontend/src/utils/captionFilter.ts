// Pure editing helpers for the caption-filter popover. Every function takes a
// CaptionFilter and returns a NEW, already cleaned one (cleanFilter: nothing
// that restricts nothing survives), so the popover just emits the result and
// `activeFilterCount` / `isFilterEmpty` stay exact.
import {
  cleanFilter,
  type CaptionFilter,
  type CaptionMeta,
  type CaptionMetaColumn,
  type CaptionMetaIntRange,
  type CaptionMetaOption,
  type CaptionMetaRange,
} from '../types/t2i'

export type NumericColumn = CaptionMetaRange | CaptionMetaIntRange
export type RangeEnd = 'min' | 'max'
export type ClothingGroup = 'any' | 'none'

// The filter keys each kind of metadata column maps onto. A column outside
// these sets (a newer server, say) has nothing to edit and is not shown.
const CHOICE_KEYS = ['nudity', 'aspect_ratios'] as const
const RANGE_KEYS = ['artistic_quality', 'erotic_score', 'pornographic_score', 'males', 'females'] as const
const TAGS_KEY = 'clothing'

type ChoiceKey = (typeof CHOICE_KEYS)[number]
type RangeKey = (typeof RANGE_KEYS)[number]

const isChoiceKey = (key: string): key is ChoiceKey => (CHOICE_KEYS as readonly string[]).includes(key)
const isRangeKey = (key: string): key is RangeKey => (RANGE_KEYS as readonly string[]).includes(key)

export const isNumericColumn = (c: CaptionMetaColumn): c is NumericColumn =>
  c.type === 'range' || c.type === 'int_range'

/** The columns the popover can edit, in the order the server listed them. */
export function editableColumns(meta: CaptionMeta | null | undefined): CaptionMetaColumn[] {
  if (!meta) return []
  return meta.columns.filter((c) => {
    if (c.type === 'choice') return isChoiceKey(c.key)
    if (c.type === 'tags') return c.key === TAGS_KEY
    return isRangeKey(c.key)
  })
}

/** The values ticked in a choice column (nudity, aspect_ratios); empty for any other key. */
export function chosenValues(filter: CaptionFilter, key: string): string[] {
  return isChoiceKey(key) ? (filter[key] ?? []) : []
}

/** Ticks or unticks one value of a choice column (nudity, aspect_ratios). */
export function toggleChoice(filter: CaptionFilter, key: string, value: string): CaptionFilter {
  if (!isChoiceKey(key)) return cleanFilter(filter)
  const current = filter[key] ?? []
  const next = current.includes(value) ? current.filter((v) => v !== value) : [...current, value]
  return cleanFilter({ ...filter, [key]: next })
}

/** One end of a numeric range, undefined when that end is not set. */
export function rangeEndValue(filter: CaptionFilter, key: string, end: RangeEnd): number | undefined {
  return isRangeKey(key) ? filter[key]?.[end] : undefined
}

/**
 * Sets one end of a numeric range from what the user typed. Blank clears the
 * end; text that is not a number changes nothing. The value is rounded for an
 * integer column and clamped into the column's own bounds, then held back by
 * the other end so min can never pass max (the server answers 400 to that).
 * An end that sits at the column's extreme restricts nothing and is dropped.
 */
export function setRangeEnd(
  filter: CaptionFilter,
  column: NumericColumn,
  end: RangeEnd,
  raw: string,
): CaptionFilter {
  const key = column.key
  if (!isRangeKey(key)) return cleanFilter(filter)
  const range = { ...filter[key] }
  const text = raw.trim()
  if (text === '') {
    delete range[end]
  } else {
    let n = Number(text)
    if (!Number.isFinite(n)) return cleanFilter(filter)
    if (column.type === 'int_range') n = Math.round(n)
    n = Math.min(column.max, Math.max(column.min, n))
    const other = end === 'min' ? range.max : range.min
    if (other !== undefined) n = end === 'min' ? Math.min(n, other) : Math.max(n, other)
    if ((end === 'min' && n <= column.min) || (end === 'max' && n >= column.max)) delete range[end]
    else range[end] = n
  }
  return cleanFilter({ ...filter, [key]: range })
}

/** Which clothing group an item is in, if any. */
export function clothingGroupOf(filter: CaptionFilter, item: string): ClothingGroup | null {
  if (filter.clothing_any?.includes(item)) return 'any'
  if (filter.clothing_none?.includes(item)) return 'none'
  return null
}

/**
 * Ticks or unticks an item in "has any" or "has none". An item is wanted or
 * unwanted, never both, so ticking it in one group removes it from the other.
 */
export function toggleClothing(filter: CaptionFilter, group: ClothingGroup, item: string): CaptionFilter {
  const here = group === 'any' ? 'clothing_any' : 'clothing_none'
  const there = group === 'any' ? 'clothing_none' : 'clothing_any'
  const held = filter[here] ?? []
  const next: CaptionFilter = { ...filter }
  next[here] = held.includes(item) ? held.filter((v) => v !== item) : [...held, item]
  next[there] = (filter[there] ?? []).filter((v) => v !== item)
  return cleanFilter(next)
}

/** Every ticked clothing item, the "has any" ones first (the popover's chips). */
export function pickedClothing(filter: CaptionFilter): { group: ClothingGroup; value: string }[] {
  return [
    ...(filter.clothing_any ?? []).map((value) => ({ group: 'any' as const, value })),
    ...(filter.clothing_none ?? []).map((value) => ({ group: 'none' as const, value })),
  ]
}

/** The options whose value contains the query (case-insensitive, trimmed); server order kept. */
export function searchOptions(options: CaptionMetaOption[], query: string): CaptionMetaOption[] {
  const q = query.trim().toLowerCase()
  return q ? options.filter((o) => o.value.toLowerCase().includes(q)) : options
}
