// Prompt-writer adapters (taste-learning LoRA fine-tunes of the writer model)
// and the Writer menus of the T2I / I2V dialogs.

import type { WriterStats } from './taste'

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

/** Favorite rate of `writerId` over every window of the writer stats (seen generations), or null. */
export function writerFavRate(stats: WriterStats | null, kind: 't2i' | 'i2v', writerId: string): number | null {
  let seen = 0
  let fav = 0
  for (const w of stats?.windows ?? []) {
    if (w.kind !== kind || w.writer_id !== writerId) continue
    seen += w.n_seen
    fav += w.n_fav
  }
  return seen > 0 ? fav / seen : null
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
  stats: WriterStats | null
}

/** `name · date · fav %`, plus `· untested` for an adapter not yet judged by a scorecard. */
export function adapterLabel(a: WriterAdapter, rate: number | null): string {
  const date = (a.created_at || '').slice(0, 10)
  const fav = rate === null ? '— fav' : `${Math.round(rate * 100)}% fav`
  const parts = [a.name, date, fav].filter((p) => p)
  return parts.join(' · ') + (a.status === 'untested' ? ' · untested' : '')
}

/**
 * The adapters a Writer menu offers: servable, of the dialog's kind, scoped to
 * `all` or one of the selected categories. One with fewer than
 * MIN_TARGET_MODEL_EXAMPLES examples for the selected T2I model is listed
 * disabled (an adapter without recorded per-model counts is not).
 */
export function adapterMenuOptions(adapters: WriterAdapter[], ctx: AdapterMenuContext): WriterOption[] {
  const scopes = new Set(ctx.categoryIds.map(String))
  const out: WriterOption[] = []
  for (const a of adapters) {
    if (a.kind !== ctx.kind || !SERVABLE_STATUSES.includes(a.status)) continue
    if (a.scope !== 'all' && !scopes.has(a.scope)) continue
    const perModel = a.counts?.by_target_model
    const n = ctx.targetModel && perModel ? (perModel[ctx.targetModel] ?? 0) : null
    const disabled = n !== null && n < MIN_TARGET_MODEL_EXAMPLES
    out.push({
      id: a.writer_id,
      label: adapterLabel(a, writerFavRate(ctx.stats, ctx.kind, a.writer_id)),
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
