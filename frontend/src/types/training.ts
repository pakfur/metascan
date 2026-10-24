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
  /** GPU memory allocated now / peak of the current stage (GiB). */
  alloc_gib?: number | null
  peak_gib?: number | null
  /** The VRAM-cap rehearsal applied to this attempt (GiB), or null. */
  vram_cap_gb?: number | null
  /** The card's total (GiB) when the requested cap was at or above it and so ignored; else null. */
  vram_cap_ignored_total_gib?: number | null
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

/**
 * GET /api/writers/training. `dataset` / `next_name` come only with the full
 * payload (the window); the light status (`dataset=0`, the always-on chip
 * and GPU lock) leaves them out and never builds the dataset.
 */
export interface TrainingStatus {
  state: 'idle' | RunState
  gpu_locked: boolean
  run: ActiveRun | null
  runs: ResumableRun[]
  dataset?: DatasetSummary
  next_name?: string
  gate: TrainingGate
  presets: Record<TrainingEffort, Hyperparams>
  clamps: Record<OverrideKey, [number, number]>
  /** The VRAM-cap rehearsal knob: the config default (models.writer_train_vram_cap_gb) and choices. */
  vram_cap?: VramCapInfo
}

export interface VramCapInfo {
  default: number | null
  choices: number[]
  min: number
}

export interface StartTrainingRequest {
  scope: string
  effort: TrainingEffort
  overrides?: Overrides
  start_from: 'fresh' | number
  render_scorecard: boolean
  /** Omitted: the config default; 0: off; else a cap in GB (rehearse a smaller card). */
  vram_cap_gb?: number
}

/** One stage of a training attempt's profile (numbers only). */
export interface ProfileStage {
  name: 'load' | 'sft' | 'dpo' | 'eval' | 'convert' | string
  completed: boolean
  wall_s: number
  steps_done: number
  samples_per_s: number | null
  tokens_per_s: number | null
  peak_allocated_gib: number | null
  peak_reserved_gib: number | null
  [key: string]: unknown
}

/** One attempt's profile (worker profile.json, schema 1). Never holds example text. */
export interface TrainingProfile {
  schema: number
  attempt: {
    run_id: number | null
    started_at: string
    ended_at: string | null
    wall_s: number | null
    outcome: 'completed' | 'paused' | 'cancelled' | 'failed' | 'oom' | null
    exit_code: number | null
    resumed: boolean
    resumed_from_step: number | null
    [key: string]: unknown
  }
  environment: Record<string, unknown>
  config: Record<string, unknown>
  dataset: Record<string, unknown>
  stages: ProfileStage[]
  timeline?: Record<string, unknown[]>
  oom: Record<string, unknown> | null
  adapter_id?: number | null
  adapter_name?: string | null
}

/** GET /api/writers/training/profiles */
export interface TrainingProfiles {
  schema: number
  max_entries: number
  /** Newest first, without timelines. */
  profiles: TrainingProfile[]
  /** The running attempt (live) or the last one, with its timeline. */
  current: TrainingProfile | null
}

/** Per adapter: the peak VRAM of the attempt that completed it and the train time of all its attempts. */
export interface AdapterProfileStats {
  peak_gib: number | null
  train_s: number
}

export function adapterProfileStats(profiles: TrainingProfile[]): Map<number, AdapterProfileStats> {
  const out = new Map<number, AdapterProfileStats>()
  for (const p of profiles) {
    const id = p.adapter_id
    if (typeof id !== 'number') continue
    const cur = out.get(id) ?? { peak_gib: null, train_s: 0 }
    cur.train_s += typeof p.attempt?.wall_s === 'number' ? p.attempt.wall_s : 0
    if (p.attempt?.outcome === 'completed') {
      const peaks = (p.stages ?? []).map((s) => s.peak_allocated_gib).filter((v): v is number => typeof v === 'number')
      if (peaks.length) cur.peak_gib = Math.max(cur.peak_gib ?? 0, ...peaks)
    }
    out.set(id, cur)
  }
  return out
}

/** "1h 05m", "12m", "40s" (a duration, not an ETA). */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds < 0) return ''
  const s = Math.round(seconds)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m`
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m`
}

/** GET /api/writers/briefs/status */
export interface BriefsStatus {
  running: boolean
  done: number
  total: number
  failed: number
  result: string | null
}

export type PreviewKind = 'preference' | 'edit' | 'hindsight'

/** GET /api/writers/dataset/preview — owner-only, never logged. */
export interface PreviewExample {
  kind: PreviewKind
  system: string
  user: string
  chosen: string
  rejected?: string
  target_model: string
  categories: string[]
  source: string
  group: string
}

/** Up to 10 examples balanced across preference / edit / hindsight. */
export interface DatasetPreview {
  scope: string
  examples: PreviewExample[]
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
