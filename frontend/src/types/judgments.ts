// Taste judgments (rating capture). Reasons are in key order: key 1 is
// REASONS[0], key 6 is REASONS[5].
export const REASONS = ['realism', 'composition', 'emotion', 'light', 'concept', 'motion'] as const
export type Reason = (typeof REASONS)[number]
// ✓ pass / ✗ fail: the training signal. Independent of ★ favorite (a view
// marker only); neither ever sets or clears the other.
export type Grade = 'pass' | 'fail'

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
  favorite: boolean
  grade: Grade | null
  // What worked (✓) or missed (✗); always empty without a grade.
  reasons: Reason[]
  seen_at: string | null
  // When the grade was last set.
  judged_at: string | null
  confirmed_at: string | null
  categories: CategoryRef[]
  // Position of a T2I image in its generation batch. Only GET returns the
  // key (PATCH / seen responses omit it), so undefined means "unknown",
  // not "no batch"; null means the item has no batch.
  batch?: BatchPosition | null
  // A writer-adapter scorecard image (GET only, like `batch`): hidden as
  // "judge to reveal" while `blind`. null: not one.
  scorecard?: { id: string; blind: boolean } | null
}

export interface BatchPosition {
  index: number
  size: number
}

// An image counts as seen after this long on screen; a video on play.
export const SEEN_DWELL_MS = 1000
