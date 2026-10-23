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
  new_events: number
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
