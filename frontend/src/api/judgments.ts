import type { JudgmentState, Reason, Verdict } from '../types/judgments'
import { get, post, patch } from './client'

// Paths go through encodeURIComponent like api/media.ts; the backend routes
// use `{file_path:path}`.
function judgmentPath(filePath: string): string {
  return `/judgments/${encodeURIComponent(filePath)}`
}

export function fetchJudgment(filePath: string): Promise<JudgmentState> {
  return get<JudgmentState>(judgmentPath(filePath))
}

// `scoreShown`: the taste score on screen at dwell time (two decimals).
export function markSeen(filePath: string, scoreShown?: number): Promise<JudgmentState> {
  const body: Record<string, unknown> = { file_path: filePath }
  if (scoreShown !== undefined) body.score_shown = scoreShown
  return post<JudgmentState>('/judgments/seen', body)
}

export function setVerdict(
  filePath: string,
  verdict: Verdict | null,
  opts: { scoreShown?: number; stickyCategoryId?: number } = {},
): Promise<JudgmentState> {
  const body: Record<string, unknown> = { verdict }
  if (opts.scoreShown !== undefined) body.score_shown = opts.scoreShown
  if (opts.stickyCategoryId !== undefined) body.sticky_category_id = opts.stickyCategoryId
  return patch<JudgmentState>(judgmentPath(filePath), body)
}

export function setReason(
  filePath: string,
  reason: Reason,
  on: boolean,
  scoreShown?: number,
): Promise<JudgmentState> {
  const body: Record<string, unknown> = { reason, on }
  if (scoreShown !== undefined) body.score_shown = scoreShown
  return patch<JudgmentState>(judgmentPath(filePath), body)
}
