// Taste judgments (rating capture). Reasons are in key order: key 1 is
// REASONS[0], key 6 is REASONS[5].
export const REASONS = ['realism', 'composition', 'emotion', 'light', 'concept', 'motion'] as const
export type Reason = (typeof REASONS)[number]
export type Verdict = 'favorite' | 'pass'

export interface CategoryRef {
  id: number
  name: string
}

export interface Category extends CategoryRef {
  item_count: number
  created_at: string
}

export interface JudgmentState {
  file_path: string
  verdict: Verdict | null
  reasons: Reason[]
  seen_at: string | null
  confirmed_at: string | null
  is_favorite: boolean
  categories: CategoryRef[]
  // Position of a T2I image in its generation batch. Only GET returns the
  // key (PATCH / seen responses omit it), so undefined means "unknown",
  // not "no batch"; null means the item has no batch.
  batch?: BatchPosition | null
}

export interface BatchPosition {
  index: number
  size: number
}

// An image counts as seen after this long on screen; a video on play.
export const SEEN_DWELL_MS = 1000
