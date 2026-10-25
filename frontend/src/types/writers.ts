// Prompt-writer adapters (taste-learning LoRA fine-tunes of the writer model)
// and the Writer menus of the T2I / I2V dialogs.

export type WriterAdapterStatus = 'training' | 'paused' | 'untested' | 'ready' | 'failed' | 'deleted'

/** An `adapter:<id>` writer id (the lineage id of registry adapter <id>). */
export type AdapterWriterId = `adapter:${number}`

/** GET /api/writers/adapters: one registry row (deleted ones are not listed). */
export interface WriterAdapter {
  id: number
  name: string
  /** 'all' or the id of the one category its examples carry. */
  scope: string
  kind: 't2i' | 'i2v'
  base_model: string
  status: WriterAdapterStatus
  created_at: string
  data_cutoff: string | null
  hyperparams: Record<string, unknown>
  /** Dataset counts; `by_target_model` (T2I model id → examples) once recorded. */
  counts: { by_target_model?: Record<string, number> } & Record<string, unknown>
  holdout_agreement: number | null
  base_agreement: number | null
  file_path: string | null
  error: string | null
  deleted_at: string | null
  writer_id: AdapterWriterId
}

/** Statuses an adapter serves drafts in (the writer's llama-server loads it). */
export const SERVABLE_STATUSES: WriterAdapterStatus[] = ['ready', 'untested']

/** Below this many examples for the selected T2I model an adapter is offered disabled. */
export const MIN_TARGET_MODEL_EXAMPLES = 50

/** 409 detail code when a request names an adapter the writer cannot serve. */
export const ADAPTER_UNAVAILABLE = 'adapter_unavailable'
/** 409 detail code: the adapter is registered, but the busy writer has not loaded it yet. */
export const ADAPTER_LOADING = 'adapter_loading'
export const ADAPTER_LOADING_TEXT = 'The writer is busy — try again when the current batch finishes.'

/** The `adapter:<id>` ids of the servable adapters of `kind`. */
export function servableAdapterIds(adapters: WriterAdapter[], kind: 't2i' | 'i2v'): string[] {
  return adapters.filter((a) => a.kind === kind && SERVABLE_STATUSES.includes(a.status)).map((a) => a.writer_id)
}

/** The adapter (`adapter:<id>`) a 409 adapter refusal names, or null. */
export function adapterErrorWriter(e: unknown): string | null {
  const err = e as { detail?: unknown } | null
  const id = (err?.detail as { writer_id?: unknown } | null)?.writer_id
  return isAdapterWriterId(id) ? id : null
}

/** The 409 detail code of an adapter refusal, or null. */
export function adapterErrorCode(e: unknown): string | null {
  const err = e as { status?: unknown; detail?: unknown } | null
  if (!err || err.status !== 409) return null
  const code = (err.detail as { code?: unknown } | null)?.code
  return code === ADAPTER_UNAVAILABLE || code === ADAPTER_LOADING ? code : null
}

export function isAdapterWriterId(id: unknown): id is AdapterWriterId {
  return typeof id === 'string' && /^adapter:[1-9]\d*$/.test(id)
}

/** One entry of a Writer menu. */
export interface WriterOption {
  id: string
  label: string
  disabled: boolean
  /** Tooltip: why it is disabled. */
  title: string
  untested: boolean
}

export interface AdapterMenuContext {
  kind: 't2i' | 'i2v'
  /** The dialog's selected categories: an adapter scoped to one shows only when it is among them. */
  categoryIds: number[]
  /** The selected T2I model (per-target-model example counts); null for I2V. */
  targetModel: string | null
  /** The blind scorecard results (GET /api/writers/scorecards), or null before they load. */
  scorecards: ScorecardSummary | null
}

/**
 * `name · date · 31% pass` — the pass rate on the adapter's blind
 * scorecard, left out while nothing is judged — plus `· untested · 12/30`
 * (judged pairs) for an adapter not yet promoted by its scorecard.
 */
export function adapterLabel(a: WriterAdapter, result: ScorecardResult | null | undefined, readyPairs: number): string {
  const date = (a.created_at || '').slice(0, 10)
  const rate = result?.pass_rate_adapter ?? null
  const parts = [a.name, date, rate === null ? '' : passText(rate)].filter((p) => p)
  if (a.status === 'untested') parts.push('untested', judgedPairs(result?.n_judged ?? 0, readyPairs))
  return parts.join(' · ')
}

/**
 * The menu label of a built-in writer: Base carries `· baseline · 19% pass`,
 * the base writer's pass rate over every judged scorecard pair, once there
 * is one (the scorecard compares adapters against plain Base).
 */
export function builtinWriterLabel(id: string, label: string, scorecards: ScorecardSummary | null): string {
  const rate = id === 'base' ? (scorecards?.base.pass_rate ?? null) : null
  return rate === null ? label : `${label} · baseline · ${passText(rate)}`
}

/**
 * The adapters a Writer menu offers: servable, of the dialog's kind, scoped to
 * `all` or one of the selected categories. One with fewer than
 * MIN_TARGET_MODEL_EXAMPLES examples for the selected T2I model is listed
 * disabled (an adapter without recorded per-model counts is not).
 */
export function adapterMenuOptions(adapters: WriterAdapter[], ctx: AdapterMenuContext): WriterOption[] {
  const scopes = new Set(ctx.categoryIds.map(String))
  const results = new Map((ctx.scorecards?.adapters ?? []).map((r) => [r.adapter_id, r]))
  const readyPairs = ctx.scorecards?.ready_pairs ?? SCORECARD_READY_PAIRS
  const out: WriterOption[] = []
  for (const a of adapters) {
    if (a.kind !== ctx.kind || !SERVABLE_STATUSES.includes(a.status)) continue
    if (a.scope !== 'all' && !scopes.has(a.scope)) continue
    const perModel = a.counts?.by_target_model
    const n = ctx.targetModel && perModel ? (perModel[ctx.targetModel] ?? 0) : null
    const disabled = n !== null && n < MIN_TARGET_MODEL_EXAMPLES
    out.push({
      id: a.writer_id,
      label: adapterLabel(a, results.get(a.id), readyPairs),
      disabled,
      title: disabled
        ? `Trained on only ${n} example${n === 1 ? '' : 's'} for ${ctx.targetModel} (needs ${MIN_TARGET_MODEL_EXAMPLES})`
        : '',
      untested: a.status === 'untested',
    })
  }
  return out
}

/** The display name of a writer id: an adapter's name when it is known. */
export function writerName(adapters: WriterAdapter[], writerId: string): string {
  if (!isAdapterWriterId(writerId)) return writerId
  return adapters.find((a) => a.writer_id === writerId)?.name ?? writerId
}

// ---- scorecard: base vs adapter on fixed briefs, judged blind ----------------

/** Judged pairs that make an untested adapter ready. */
export const SCORECARD_READY_PAIRS = 30

export interface ScorecardRun {
  id: string
  status: 'running' | 'complete' | 'incomplete' | 'interrupted' | 'cancelled' | 'failed'
  target_model: string
  done: number
  total: number
  error: string | null
  created_at: string
  updated_at: string
}

/** One adapter's result over its scorecards (GET /api/writers/scorecards rows). */
export interface ScorecardResult {
  adapter_id: number
  name?: string
  status?: WriterAdapterStatus
  n_pairs: number
  /**
   * Pairs whose two images both have a grade (✓ pass or ✗ fail; ★ and a
   * view do not count), deleted images included.
   */
  n_judged: number
  /** ✓ among each writer's images of the judged pairs. */
  pass_base: number
  pass_adapter: number
  /** pass_* / n_judged, null while nothing is judged. */
  pass_rate_base: number | null
  pass_rate_adapter: number | null
  mean_score_base: number | null
  mean_score_adapter: number | null
  latest?: ScorecardRun | null
}

/** The scorecard run in progress (WS `scorecard_progress`). */
export interface ScorecardProgress {
  adapter_id: number
  scorecard_id: string
  phase: 'prompting' | 'rendering'
  done: number
  total: number
}

export interface ScorecardSummary {
  briefs: number
  ready_pairs: number
  running: ScorecardProgress | null
  base: { n_judged: number; pass_rate: number | null; mean_score: number | null }
  adapters: ScorecardResult[]
}

/** "31%" / "—". */
export function ratePct(rate: number | null | undefined): string {
  return rate === null || rate === undefined ? '—' : `${Math.round(rate * 100)}%`
}

/** "31% pass" / "— pass": a scorecard pass rate, as the Writer menu and the training window show it. */
export function passText(rate: number | null | undefined): string {
  return `${ratePct(rate)} pass`
}

/** "12/30": judged scorecard pairs toward promotion (capped at `readyPairs`). */
export function judgedPairs(nJudged: number, readyPairs: number): string {
  return `${Math.min(nJudged, readyPairs)}/${readyPairs}`
}
