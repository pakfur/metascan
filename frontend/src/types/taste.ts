export interface TasteModelInfo {
  id: number
  created_at: string
  n_pos: number
  n_neg: number
  n_pairs: number
  holdout_auc: number | null
  embedding_model: string
  half_life_days: number
}

export interface TasteMissing {
  positives: number
  negatives: number
}

export interface TasteStatus {
  active_model: TasteModelInfo | null
  stale: boolean
  training: boolean
  // Label-changing events since the active model's snapshot; null when
  // there is no model yet (the first one trains once `missing` is null).
  new_events: number | null
  missing: TasteMissing | null
  // 'labels': too few ratings; 'vectors': enough ratings, but too few of
  // them have a CLIP vector yet (index missing or mid-rebuild).
  missing_reason: 'labels' | 'vectors' | null
  auto_retrain: boolean
  last_error: string | null
}

export interface TasteScoresResponse {
  model_id: number | null
  scores: Record<string, number>
}

// `taste` WS channel payloads.
export type TasteWsEvent =
  | 'training_started'
  | 'training_complete'
  | 'training_failed'
  | 'scores_updated'

// GET /api/taste/writer-stats: favorite rate among SEEN generations per
// writer per 14-day window (by generation date, UTC), plus favorite diversity.
export interface WriterWindowStat {
  kind: 't2i' | 'i2v'
  // A writer id ('base', 'base+taste', 'fallback', 'manual', ...) or
  // 'unknown' for generations recorded before writer lineage existed.
  writer_id: string
  window_start: string // ISO date
  n_seen: number
  n_fav: number
  rate: number | null // null when n_seen is 0
}

// Mean pairwise CLIP cosine distance between the window's favorited T2I
// images, from the last training run; null when fewer than 2 have a vector.
export interface DiversityWindow {
  window_start: string
  n: number
  mean_distance: number | null
  computed_at: string // when the training run that stored it ran
}

export interface WriterStats {
  window_days: number
  window_starts: string[]
  windows: WriterWindowStat[]
  diversity: DiversityWindow[]
  collapse_warning: boolean
}
