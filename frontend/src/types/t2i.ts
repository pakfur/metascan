import type { Media } from './media'
import type { ThumbItem } from './jobs'

// Reused, not copied: a select's options plus a stored value that has since
// left the config list (types/i2v.ts owns the one implementation).
export { withCurrentOption } from './i2v'

// ---- config ---------------------------------------------------------------

export type T2iMode = 'manual' | 'random'
export type SeedPolicy = 'fixed' | 'increment' | 'decrement' | 'random'
export type IdentityStyle = 'ref' | 'noun' | 'name'
export type T2iContentMode = 'uncensored' | 'sfw' | 'default'

export interface T2iModelInfo {
  id: string
  label: string
  has_negative: boolean
  identity: IdentityStyle
}

/** GET /api/t2i/config: the sanitised `t2i` config section plus server-derived facts. */
export interface T2iConfig {
  output_root: string
  output_prefix: string
  megapixels: number[]
  default_megapixels: number
  default_model: string
  model_workflows: Record<string, number | null>
  content_mode: T2iContentMode
  identity: Record<string, IdentityStyle>
  window: number
  max_batch_size: number
  max_count_per_batch: number
  models: T2iModelInfo[]
  aspect_ratios: string[]
  seed_policies: SeedPolicy[]
  seed_max: number
  csv: { available: boolean; total: number; error: string | null }
  wildcards: { slots: string[]; warnings: string[] }
}

/** GET /api/t2i/output-preview: always 200, problems come back as `error`. */
export interface T2iOutputPreview {
  path: string | null
  error: string | null
  warnings: string[]
}

// Used when the config has not loaded yet; the same defaults get_t2i_config applies.
export const DEFAULT_MAX_BATCH_SIZE = 500
export const DEFAULT_MAX_COUNT_PER_BATCH = 32

// ---- caption filter and meta ------------------------------------------------

/** A numeric filter range: the server takes "min and/or max", so either end may be left out. */
export interface Range {
  min?: number
  max?: number
}

/**
 * POST /api/t2i/captions/{count,random} and BatchRequest.filter. Every key is
 * optional and an absent key means "no restriction"; cleanFilter removes what
 * restricts nothing (empty lists, ranges with no usable end) so the wire shape
 * stays minimal. The meta column `clothing` (type "tags") maps to the two keys
 * clothing_any / clothing_none.
 */
export interface CaptionFilter {
  nudity?: string[]
  artistic_quality?: Range
  erotic_score?: Range
  pornographic_score?: Range
  males?: Range
  females?: Range
  aspect_ratios?: string[]
  clothing_any?: string[]
  clothing_none?: string[]
}

export interface CaptionMetaOption {
  value: string
  count: number
}

/** A choice column (nudity, aspect_ratios) or the clothing tag list, options by count desc. */
export interface CaptionMetaChoice {
  key: string
  label: string
  type: 'choice' | 'tags'
  options: CaptionMetaOption[]
}

/** A float score column (artistic_quality, erotic_score, pornographic_score), step 0.05. */
export interface CaptionMetaRange {
  key: string
  label: string
  type: 'range'
  min: number
  max: number
  step: number
}

/** An integer count column (males, females). */
export interface CaptionMetaIntRange {
  key: string
  label: string
  type: 'int_range'
  min: number
  max: number
}

export type CaptionMetaColumn = CaptionMetaChoice | CaptionMetaRange | CaptionMetaIntRange

/** GET /api/t2i/captions/meta. Only columns present in the CSV appear. */
export interface CaptionMeta {
  total: number
  columns: CaptionMetaColumn[]
}

export interface CaptionCount {
  count: number
  total: number
}

/** One CSV row, as POST /api/t2i/captions/random returns it. */
export interface CaptionRow {
  id: number
  caption: string
  aspect_ratio: string
  nudity: string | null
  artistic_quality: number | null
  erotic_score: number | null
  pornographic_score: number | null
  males: number | null
  females: number | null
  clothing: string[]
}

const FILTER_LIST_KEYS = ['nudity', 'aspect_ratios', 'clothing_any', 'clothing_none'] as const
const FILTER_RANGE_KEYS = [
  'artistic_quality',
  'erotic_score',
  'pornographic_score',
  'males',
  'females',
] as const

export function emptyFilter(): CaptionFilter {
  return {}
}

/**
 * A copy of the filter with everything that means "no restriction" removed:
 * empty lists, null/missing ranges, and any range end that is not a finite
 * number (a range keeps whichever end is usable). Only the known filter keys
 * survive. This is the shape that goes on the wire.
 */
export function cleanFilter(filter: CaptionFilter | null | undefined): CaptionFilter {
  const out: CaptionFilter = {}
  if (!filter) return out
  for (const key of FILTER_LIST_KEYS) {
    const list = filter[key]
    if (Array.isArray(list) && list.length > 0) out[key] = [...list]
  }
  for (const key of FILTER_RANGE_KEYS) {
    const range = filter[key]
    if (!range) continue
    const cleaned: Range = {}
    if (typeof range.min === 'number' && Number.isFinite(range.min)) cleaned.min = range.min
    if (typeof range.max === 'number' && Number.isFinite(range.max)) cleaned.max = range.max
    if (cleaned.min !== undefined || cleaned.max !== undefined) out[key] = cleaned
  }
  return out
}

export function activeFilterCount(filter: CaptionFilter | null | undefined): number {
  return Object.keys(cleanFilter(filter)).length
}

export function isFilterEmpty(filter: CaptionFilter | null | undefined): boolean {
  return activeFilterCount(filter) === 0
}

// ---- form state, images ----------------------------------------------------

export interface T2iLora {
  name: string
  strength: number
}

/**
 * The EDITABLE copy of the dialog's form kept on each image (spec 7.3).
 * Seeded from the request at ingest, loaded when a tile is selected and
 * autosaved into. Seed policy, Batch Size and Count per Batch are
 * deliberately not part of it (session-local). The server always sends a
 * complete one; individual values can still be null, `model` included (an
 * image ingested without a recorded model), but a PATCH must never send a
 * null or empty `model`.
 */
export interface T2iFormState {
  mode: T2iMode
  filter: CaptionFilter | null
  caption: string | null
  model: string | null
  preset_id: number | null
  megapixels: number | null
  aspect_ratio: string | null
  seed: number | null
  prompt: string | null
  negative: string | null
  loras: T2iLora[]
}

/** One row of GET /api/t2i/images. The as-rendered fields are facts and never change. */
export interface T2iImage {
  id: number
  file_path: string
  file_name: string
  batch_id: string | null
  model: string | null
  preset_id: number | null
  caption: string | null
  prompt_used: string | null
  negative_used: string | null
  seed: number | null
  prompt_seed: number | null
  width: number | null
  height: number | null
  megapixels: number | null
  aspect_ratio: string | null
  loras: T2iLora[] | null
  render_s: number | null
  comfy_prompt_id: string | null
  // SQLite datetime('now'): UTC with no zone marker (see formatT2iTimestamp).
  created_at: string
  is_favorite: boolean
  form_state: T2iFormState
}

/** POST /api/t2i/prompt. Review-only: nothing is written. */
export interface T2iPromptResult {
  prompt: string
  negative: string | null
  resolved_caption: string
  warnings: string[]
}

/** POST /api/t2i/captions/resolve. */
export interface T2iResolveResult {
  resolved_caption: string
  characters: Record<string, Record<string, string>>
  warnings: string[]
}

// ---- batches ---------------------------------------------------------------

/**
 * POST /api/t2i/batches body. caption / prompt / negative / aspect_ratio are
 * Manual fields, `filter` is a Random field.
 */
export interface T2iBatchRequest {
  mode: T2iMode
  model: string
  preset_id: number
  megapixels: number
  seed: number
  seed_policy: SeedPolicy
  batch_size?: number
  count_per_batch?: number
  loras?: T2iLora[]
  caption?: string | null
  prompt?: string | null
  negative?: string | null
  aspect_ratio?: string | null
  filter?: CaptionFilter | null
}

/** POST /api/t2i/batches response. */
export interface T2iBatchStarted {
  batch_id: string
  total_images: number
  warnings: string[]
}

/** What the store's startBatch resolves with: the response plus the next unused seed, when known. */
export interface T2iStartResult extends T2iBatchStarted {
  next_seed: number | null
}

/** The current step of a batch: the caption / prompt / aspect / seed the dialog mirrors. */
export interface T2iBatchStep {
  step: number
  total_steps: number
  caption: string
  aspect_ratio: string
  seed: number
  prompt: string
  negative: string | null
  warnings: string[]
}

/** One entry of GET /api/t2i/batches. */
export interface T2iBatchInfo {
  batch_id: string
  mode: T2iMode
  state: 'prompting' | 'rendering'
  total_steps: number
  step: T2iBatchStep | null
  images_total: number
  images_done: number
  images_failed: number
  next_seed: number | null
  started_at: string
}

/** A batch the store is tracking: the server's snapshot plus the last per-image error. */
export interface T2iActiveBatch extends T2iBatchInfo {
  last_error: string | null
}

/** Why a tracked batch stopped. */
export type T2iBatchOutcome = 'complete' | 'cancelled' | 'error'

/** What the store keeps of the batch that ended most recently (the dialog unlocks from it). */
export interface T2iFinishedBatch {
  batch_id: string
  mode: T2iMode
  outcome: T2iBatchOutcome
  images_done: number
  images_failed: number
  next_seed: number | null
  // The last step's values; for a Random batch these fill Caption / Prompt / Aspect.
  step: T2iBatchStep | null
  error: string | null
}

// ---- WebSocket `t2i` channel payloads (spec section 9) ---------------------

export interface T2iBatchStartedEvent {
  batch_id: string
  mode: T2iMode
  total_steps: number
  total_images: number
}

export interface T2iBatchStepEvent extends T2iBatchStep {
  batch_id: string
}

export interface T2iBatchProgressEvent {
  batch_id: string
  phase: 'prompting' | 'rendering'
  images_done: number
  images_failed: number
  images_total: number
  next_seed: number | null
  last_error?: string | null
}

/** batch_complete and batch_cancelled. */
export interface T2iBatchEndEvent {
  batch_id: string
  images_done: number
  images_failed: number
}

export interface T2iBatchErrorEvent {
  batch_id: string
  error: string
}

export interface T2iImagesChangedEvent {
  batch_id: string
  files: string[]
}

// ---- seeds -----------------------------------------------------------------

export const SEED_MAX = 2 ** 31 - 1

/** A uniformly random seed in 0..max (inclusive). */
export function randomSeed(max: number = SEED_MAX): number {
  return Math.floor(Math.random() * (max + 1))
}

/** Pins a number into 0..max as an integer; NaN becomes 0. */
export function clampSeed(n: number, max: number = SEED_MAX): number {
  if (!Number.isFinite(n)) return 0
  return Math.min(max, Math.max(0, Math.round(n)))
}

/** Strict parse of what a user typed: digits only and within 0..max, else null. */
export function parseSeedInput(raw: string, max: number = SEED_MAX): number | null {
  const text = raw.trim()
  if (!/^\d+$/.test(text)) return null
  const n = Number(text)
  return n <= max ? n : null
}

// ---- counts ----------------------------------------------------------------

function clampInt(value: number, min: number, max: number): number {
  const n = Math.round(Number(value))
  if (!Number.isFinite(n)) return min
  return Math.min(max, Math.max(min, n))
}

/**
 * The Count per Batch the dialog actually sends. Fixed forces 1 — the same
 * seed would render the same image N times — while the field keeps whatever
 * was typed for when the policy changes back.
 */
export function effectiveCount(
  policy: SeedPolicy,
  count: number,
  max: number = DEFAULT_MAX_COUNT_PER_BATCH,
): number {
  if (policy === 'fixed') return 1
  return clampInt(count, 1, max)
}

/**
 * Whether the typed count is honoured as typed. False only when Fixed is
 * overriding a count above 1 — the moment to grey the field and say
 * "ignored while the seed is Fixed".
 */
export function isCountEffective(policy: SeedPolicy, count: number): boolean {
  return policy !== 'fixed' || clampInt(count, 1, Number.MAX_SAFE_INTEGER) === 1
}

/** The Batch Size the dialog actually sends: Manual is locked at 1. */
export function effectiveBatchSize(
  mode: T2iMode,
  size: number,
  max: number = DEFAULT_MAX_BATCH_SIZE,
): number {
  if (mode === 'manual') return 1
  return clampInt(size, 1, max)
}

/** Images one Generate produces: the "Generate ×N" number. */
export function plannedImages(
  mode: T2iMode,
  policy: SeedPolicy,
  batchSize: number,
  count: number,
  limits: { maxBatchSize?: number; maxCount?: number } = {},
): number {
  return (
    effectiveBatchSize(mode, batchSize, limits.maxBatchSize ?? DEFAULT_MAX_BATCH_SIZE) *
    effectiveCount(policy, count, limits.maxCount ?? DEFAULT_MAX_COUNT_PER_BATCH)
  )
}

// ---- image labels ------------------------------------------------------------

/** The label under a tile: "seed 321424 · 1216×832" (size omitted when unknown). */
export function t2iImageDetails(img: Pick<T2iImage, 'seed' | 'width' | 'height'>): string {
  const parts = [`seed ${img.seed ?? '—'}`]
  if (img.width && img.height) parts.push(`${img.width}×${img.height}`)
  return parts.join(' · ')
}

const TITLE_PROMPT_CHARS = 160

/** A tile's tooltip: the model, then the start of the prompt collapsed onto one line. */
export function t2iImageTitle(img: Pick<T2iImage, 'model' | 'prompt_used'>): string {
  const lines = [img.model ?? 'unknown model']
  const prompt = (img.prompt_used ?? '').replace(/\s+/g, ' ').trim()
  if (prompt) {
    lines.push(
      prompt.length > TITLE_PROMPT_CHARS ? `${prompt.slice(0, TITLE_PROMPT_CHARS)}…` : prompt,
    )
  }
  return lines.join('\n')
}

export function toStripItem(img: T2iImage): ThumbItem {
  return {
    id: img.id,
    file_path: img.file_path,
    is_favorite: img.is_favorite,
    title: t2iImageTitle(img),
    label: t2iImageDetails(img),
  }
}

/**
 * A library-shaped Media for MediaViewer: the viewer reads only the path, the
 * name, the favourite flag and the video flag, so the size and timing fields
 * a T2I row does not carry are neutral.
 */
export function t2iImageToMedia(img: T2iImage): Media {
  return {
    file_path: img.file_path,
    file_name: img.file_name,
    is_favorite: img.is_favorite,
    is_video: false,
    playback_speed: null,
    width: img.width ?? 0,
    height: img.height ?? 0,
    file_size: 0,
    frame_rate: null,
    duration: null,
    media_type: 'image',
  }
}

/**
 * SQLite's datetime('now') is UTC, space-separated, with no zone marker. Read
 * as-is a browser treats it as LOCAL time, so the marker is added before
 * parsing. Strings that already carry a zone are left alone. Null on garbage.
 */
export function parseSqliteUtc(raw: string | null | undefined): Date | null {
  if (!raw) return null
  const iso = /([zZ]|[+-]\d\d:?\d\d)$/.test(raw) ? raw : `${raw.replace(' ', 'T')}Z`
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? null : d
}

/** created_at formatted into the viewer's locale (mirrors formatI2vTimestamp). */
export function formatT2iTimestamp(raw: string | null | undefined): string {
  const d = parseSqliteUtc(raw)
  if (!d) return raw ?? ''
  return d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

// ---- image list paging -------------------------------------------------------

/**
 * Folds a freshly fetched first page into the list already held. A short page
 * is the whole library. A full page replaces everything at or above its last
 * id and keeps the older rows the user already paged in behind it, so a
 * refresh during a long batch never collapses "load older".
 */
export function mergeImagePage(
  existing: T2iImage[],
  fresh: T2iImage[],
  pageSize: number,
  prevHasMore: boolean,
): { images: T2iImage[]; hasMore: boolean } {
  if (fresh.length < pageSize) return { images: fresh, hasMore: false }
  const boundary = fresh[fresh.length - 1].id
  const older = existing.filter((i) => i.id < boundary)
  return { images: [...fresh, ...older], hasMore: older.length > 0 ? prevHasMore : true }
}

/** Appends an older page, skipping ids already present; newest first. */
export function mergeOlderPage(existing: T2iImage[], page: T2iImage[]): T2iImage[] {
  const seen = new Set(existing.map((i) => i.id))
  return [...existing, ...page.filter((i) => !seen.has(i.id))].sort((a, b) => b.id - a.id)
}

// ---- status line -------------------------------------------------------------

/**
 * "Batch 12/40 · 37/160 images · 2 failed · rendering". The batch part is
 * dropped for a single-step (Manual) batch; images appear once one finished.
 * With the VLM-unload GPU order the last step's prompt is written before any
 * image renders, so the batch part reads N/N while rendering.
 */
export function batchStatusLine(b: T2iActiveBatch): string {
  const parts: string[] = []
  if (b.step && b.total_steps > 1) parts.push(`Batch ${b.step.step}/${b.total_steps}`)
  const finished = b.images_done + b.images_failed
  if (finished > 0 && b.images_total > 0) parts.push(`${finished}/${b.images_total} images`)
  if (b.images_failed > 0) parts.push(`${b.images_failed} failed`)
  parts.push(b.state === 'prompting' ? 'writing prompts' : 'rendering')
  return parts.join(' · ')
}

// ---- scratch-form draft --------------------------------------------------------

export const DRAFT_STORAGE_KEY = 'metascan.t2i.draft.v1'

/**
 * What a stored draft is trusted to be: only fields of the right type
 * survive, so a hand-edited or older draft can never put a bad value in the
 * form. Null when nothing usable is left.
 */
export function sanitizeDraft(raw: unknown): Partial<T2iFormState> | null {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null
  const r = raw as Record<string, unknown>
  const out: Partial<T2iFormState> = {}

  if (r.mode === 'manual' || r.mode === 'random') out.mode = r.mode
  for (const key of ['caption', 'prompt', 'negative'] as const) {
    const v = r[key]
    if (typeof v === 'string' || v === null) out[key] = v
  }
  if (typeof r.model === 'string' && r.model) out.model = r.model
  if (r.preset_id === null || (typeof r.preset_id === 'number' && Number.isInteger(r.preset_id))) {
    out.preset_id = r.preset_id
  }
  if (
    r.megapixels === null ||
    (typeof r.megapixels === 'number' && Number.isFinite(r.megapixels) && r.megapixels > 0)
  ) {
    out.megapixels = r.megapixels
  }
  if (
    r.aspect_ratio === null ||
    (typeof r.aspect_ratio === 'string' && /^\d+:\d+$/.test(r.aspect_ratio))
  ) {
    out.aspect_ratio = r.aspect_ratio
  }
  if (
    r.seed === null ||
    (typeof r.seed === 'number' && Number.isInteger(r.seed) && r.seed >= 0 && r.seed <= SEED_MAX)
  ) {
    out.seed = r.seed
  }
  if (r.filter === null) {
    out.filter = null
  } else if (r.filter && typeof r.filter === 'object' && !Array.isArray(r.filter)) {
    out.filter = cleanFilter(r.filter as CaptionFilter)
  }
  if (Array.isArray(r.loras)) {
    out.loras = r.loras.flatMap((entry: unknown) => {
      const e = entry as { name?: unknown; strength?: unknown } | null
      return e &&
        typeof e.name === 'string' &&
        e.name &&
        typeof e.strength === 'number' &&
        Number.isFinite(e.strength)
        ? [{ name: e.name, strength: e.strength }]
        : []
    })
  }
  return Object.keys(out).length > 0 ? out : null
}
