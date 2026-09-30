import { get, post, patch, del } from './client'
import type {
  CaptionCount,
  CaptionFilter,
  CaptionMeta,
  CaptionRow,
  T2iBatchInfo,
  T2iBatchRequest,
  T2iBatchStarted,
  T2iConfig,
  T2iFormState,
  T2iImage,
  T2iOutputPreview,
  T2iPromptResult,
  T2iResolveResult,
} from '../types/t2i'

export function fetchT2iConfig(): Promise<T2iConfig> {
  return get('/t2i/config')
}

export function fetchCaptionMeta(): Promise<CaptionMeta> {
  return get('/t2i/captions/meta')
}

// How many CSV rows match. An empty filter matches every row.
export function countCaptions(filter: CaptionFilter = {}): Promise<CaptionCount> {
  return post('/t2i/captions/count', { filter })
}

// One random matching row. Answers 404 (ApiError) when nothing matches.
export function randomCaption(filter: CaptionFilter = {}): Promise<CaptionRow> {
  return post('/t2i/captions/random', { filter })
}

// The caption with its __TOKEN__s resolved for this seed and model. Review
// only; nothing is written.
export function resolveCaption(body: {
  caption: string
  seed: number
  model: string
}): Promise<T2iResolveResult> {
  return post('/t2i/captions/resolve', body)
}

// Rewrites the caption into the model's prompt style with the local VLM (or
// the fallback prompt, with a warning, when no VLM is available). Review
// only; nothing is written.
export function generateT2iPrompt(body: {
  caption: string
  seed: number
  model: string
}): Promise<T2iPromptResult> {
  return post('/t2i/prompt', body)
}

// Starts a server-side batch that keeps running when the dialog closes.
export function startT2iBatch(body: T2iBatchRequest): Promise<T2iBatchStarted> {
  return post('/t2i/batches', body)
}

// Batches that are still running, with counters and the current step.
export function listT2iBatches(): Promise<T2iBatchInfo[]> {
  return get('/t2i/batches')
}

// Idempotent: cancelling a batch that already ended is not an error.
export function cancelT2iBatch(batchId: string): Promise<{ status: string }> {
  return post(`/t2i/batches/${encodeURIComponent(batchId)}/cancel`)
}

// Newest first. Pass the last id of the page you hold as `beforeId` for the next one.
export function listT2iImages(limit = 60, beforeId?: number): Promise<T2iImage[]> {
  const params = new URLSearchParams({ limit: String(limit) })
  if (beforeId != null) params.set('before_id', String(beforeId))
  return get(`/t2i/images?${params.toString()}`)
}

// Autosave into one image's editable form state. Partial; the as-rendered
// facts are not reachable through this. Answers the whole updated row.
export function patchT2iImage(id: number, fields: Partial<T2iFormState>): Promise<T2iImage> {
  return patch(`/t2i/images/${id}`, fields)
}

// Deletes the row and its library media; the server moves the file to the OS trash.
export function deleteT2iImage(id: number): Promise<{ status: 'deleted' }> {
  return del(`/t2i/images/${id}`)
}

// Output paths of every T2I image still in the library — the "Generated with
// T2I" smart-folder rule's membership set.
export function listT2iPaths(): Promise<string[]> {
  return get('/t2i/paths')
}

// Where an image generated right now would land for these (possibly unsaved)
// settings. Always resolves: problems come back as `error`.
export function t2iOutputPreview(root: string, prefix: string): Promise<T2iOutputPreview> {
  const q = `root=${encodeURIComponent(root)}&prefix=${encodeURIComponent(prefix)}`
  return get(`/t2i/output-preview?${q}`)
}
