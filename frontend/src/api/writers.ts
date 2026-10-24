import { del, get, patch, post } from './client'
import type { ScorecardResult, ScorecardSummary, WriterAdapter } from '../types/writers'

/** Every prompt-writer adapter that is not deleted, in id order. */
export function listWriterAdapters(): Promise<WriterAdapter[]> {
  return get<WriterAdapter[]>('/writers/adapters')
}

/** Rename an adapter (409 duplicate_name, 422 invalid_name). */
export function renameWriterAdapter(id: number, name: string): Promise<WriterAdapter> {
  return patch<WriterAdapter>(`/writers/adapters/${id}`, { name })
}

/**
 * Delete an adapter: its files and row (history keeps the name). 409
 * adapter_training while it trains, adapter_files_busy while the writer still
 * maps its file. A paused / failed adapter's open run is discarded with it.
 */
export function deleteWriterAdapter(id: number): Promise<{ status: string }> {
  return del<{ status: string }>(`/writers/adapters/${id}`)
}

/** Every T2I adapter's scorecard result, the base writer, the running scorecard. */
export function fetchScorecards(): Promise<ScorecardSummary> {
  return get<ScorecardSummary>('/writers/scorecards')
}

/** One adapter's scorecard result (also promotes it to ready at 30 judged pairs). */
export function fetchScorecard(id: number): Promise<ScorecardResult> {
  return get<ScorecardResult>(`/writers/adapters/${id}/scorecard`)
}

/**
 * Render (or resume: only the briefs without both images) an adapter's
 * scorecard. 202; progress on WS `training`. 409 scorecard_running,
 * gpu_busy_training, adapter_unavailable, no_workflow.
 */
export function renderScorecard(
  id: number,
  body: { target_model?: string; fresh?: boolean } = {},
): Promise<{ scorecard_id: string; total: number; todo: number; resumed: boolean }> {
  return post(`/writers/adapters/${id}/scorecard`, body)
}

export function cancelScorecard(): Promise<{ status: string }> {
  return post<{ status: string }>('/writers/scorecard/cancel')
}
