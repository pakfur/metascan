// Prompt-writer LoRA training (GET /api/writers/training and the `training`
// WS channel). Example text appears only in DatasetPreview — never render or
// log it anywhere else.

export type TrainingEffort = 'quick' | 'standard' | 'thorough'
export const TRAINING_EFFORTS: TrainingEffort[] = ['quick', 'standard', 'thorough']

/** The Advanced overrides (the server clamps them into `clamps` too). */
export type OverrideKey = 'rank' | 'lr' | 'epochs' | 'dpo_lr'
export const OVERRIDE_KEYS: OverrideKey[] = ['rank', 'lr', 'epochs', 'dpo_lr']

/** Same ranges as the backend's OVERRIDE_CLAMPS (used until the status arrives). */
export const DEFAULT_CLAMPS: Record<OverrideKey, [number, number]> = {
  rank: [4, 64],
  lr: [1e-6, 5e-4],
  epochs: [1, 5],
  dpo_lr: [1e-6, 5e-4],
}

export type Hyperparams = { rank: number; lr: number; epochs: number; dpo_lr: number } & Record<string, unknown>
export type Overrides = Partial<Record<OverrideKey, number>>

/** The worker's progress.json, as broadcast in `run_progress`. */
export interface TrainingProgress {
  stage?: 'sft' | 'dpo' | 'eval' | 'convert' | string
  step?: number
  total_steps?: number
  stage_step?: number
  stage_total?: number
  loss?: number | null
  holdout_agreement?: number | null
  base_agreement?: number | null
  eta_s?: number | null
  last_checkpoint_step?: number | null
  last_checkpoint_at?: string | null
  updated_at?: string
}

export type RunState = 'running' | 'pausing' | 'cancelling' | 'starting' | string

export interface ActiveRun {
  run_id: number
  adapter_id: number
  name: string
  scope: string
  resumed: boolean
  state: RunState
  progress: TrainingProgress
}

/** A paused or failed run (resumable / cancellable by id). */
export interface ResumableRun {
  run_id: number
  adapter_id: number
  name: string
  scope: string
  status: 'paused' | 'failed'
  step: number | null
  total_steps: number | null
  error: string | null
  updated_at: string | null
  options: Record<string, unknown> | null
}

export interface DatasetCounts {
  pref_pairs: number
  edit_pairs: number
  hindsight: number
  by_target_model: Record<string, number>
  new_since_last: number
  date_span: [string, string]
  content_mode: string
}

export interface Readiness {
  trainable: boolean
  enough_new: boolean
  missing: number
}

export interface DatasetSummary {
  scope: string
  counts: DatasetCounts
  readiness: Readiness
  holdout_pairs: number
  holdout_fraction: number
}

export interface TrainingGate {
  available: boolean
  reason: string
  reasons: string[]
  /** The dependency probe is still running (start answers 409 gate_checking). */
  checking: boolean
}

/** GET /api/writers/training */
export interface TrainingStatus {
  state: 'idle' | RunState
  gpu_locked: boolean
  run: ActiveRun | null
  runs: ResumableRun[]
  dataset: DatasetSummary
  next_name: string
  gate: TrainingGate
  presets: Record<TrainingEffort, Hyperparams>
  clamps: Record<OverrideKey, [number, number]>
}

export interface StartTrainingRequest {
  scope: string
  effort: TrainingEffort
  overrides?: Overrides
  start_from: 'fresh' | number
  render_scorecard: boolean
}

/** GET /api/writers/briefs/status */
export interface BriefsStatus {
  running: boolean
  done: number
  total: number
  failed: number
  result: string | null
}

/** GET /api/writers/dataset/preview — owner-only, never logged. */
export interface PreviewExample {
  system: string
  user: string
  chosen: string
  rejected?: string
  target_model: string
  categories: string[]
  source: string
  group: string
}

export interface DatasetPreview {
  scope: string
  prefs: PreviewExample[]
  sft: PreviewExample[]
}

// `training` WS channel events.
export type TrainingRunEvent =
  | 'run_started'
  | 'run_progress'
  | 'run_paused'
  | 'run_completed'
  | 'run_failed'
  | 'run_cancelled'
export type BriefsEvent = 'briefs_progress' | 'briefs_done' | 'briefs_cancelled' | 'briefs_error'

/** Clamp `value` into the override's range (rank / epochs rounded), like the server. */
export function clampOverride(key: OverrideKey, value: number, clamps = DEFAULT_CLAMPS): number {
  const [lo, hi] = clamps[key]
  const v = Math.min(Math.max(value, lo), hi)
  return key === 'rank' || key === 'epochs' ? Math.round(v) : v
}

/** "2h 10m", "4m", "35s". */
export function formatEta(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds < 0) return ''
  const s = Math.round(seconds)
  if (s < 60) return `${s}s`
  const m = Math.round(s / 60)
  if (m < 60) return `${m}m`
  return `${Math.floor(m / 60)}h ${m % 60}m`
}

/** Overall progress 0..100 of a run, or null when unknown. */
export function progressPct(p: TrainingProgress | null | undefined): number | null {
  if (!p || !p.total_steps || p.step === undefined) return null
  return Math.max(0, Math.min(100, Math.round((p.step / p.total_steps) * 100)))
}
