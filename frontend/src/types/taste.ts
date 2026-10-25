import type { ScorecardResult } from './writers'

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

// How many more grades a model needs: positives are ✓ pass, negatives ✗ fail.
export interface TasteMissing {
  positives: number
  negatives: number
}

// Deleted graded images being given CLIP vectors for the current model from
// their archived thumbnails (after a CLIP switch, or deleted while the index
// was not loaded). `running` is false only while it waits for the GPU
// (writer training, a generation or an index build; loading CLIP counts as
// running), and the Training tab says so; it resumes on its own.
export interface TasteReembed {
  running: boolean
  done: number
  total: number
}

export interface TasteStatus {
  active_model: TasteModelInfo | null
  stale: boolean
  // The active model was trained before ✓ / ✗ grades (on favorites and
  // views): it is due for a retrain, and its scores are served until then.
  labels_outdated: boolean
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
  // null when no re-embed pass is under way.
  reembed: TasteReembed | null
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
  | 'reembed_progress'

// `reembed_progress` payload: the status's `reembed`, null when a pass ends.
export interface TasteReembedEvent {
  reembed: TasteReembed | null
}

// GET /api/taste/writer-stats: pass rate (✓ out of ✓ + ✗) among GRADED
// generations per writer per 14-day window (by generation date, UTC), plus
// the diversity of ✓ T2I generations. ★ favorites and views count for nothing.
export interface WriterWindowStat {
  kind: 't2i' | 'i2v'
  // A writer id ('base', 'base+taste', 'fallback', 'manual', ...) or
  // 'unknown' for generations recorded before writer lineage existed.
  writer_id: string
  // The VLM model that wrote the drafts (e.g. 'qwen3vl-8b'); null for typed
  // or fallback prompts and for rows recorded before the model was.
  writer_model: string | null
  window_start: string // ISO date
  n_graded: number // ✓ pass + ✗ fail
  n_pass: number // ✓ pass
  rate: number | null // n_pass / n_graded; null when n_graded is 0
}

// Mean pairwise CLIP cosine distance between the window's ✓ passed T2I
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
  /** Per adapter with a scorecard pair: base vs adapter on the blind scorecard. */
  scorecards?: ScorecardResult[]
}
