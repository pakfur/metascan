// The pure rules behind the Text to Image dialog's form: what a fresh form
// holds, how a stored form_state (an image's, or the localStorage draft) is
// loaded into it, what a Generate sends, and when Generate is blocked.
// Nothing here touches Vue, the store or the network, so the rules can be
// checked on their own.
import {
  SEED_MAX,
  cleanFilter,
  effectiveBatchSize,
  effectiveCount,
  isFilterEmpty,
  type CaptionFilter,
  type SeedPolicy,
  type T2iBatchRequest,
  type T2iConfig,
  type T2iFinishedBatch,
  type T2iFormState,
  type T2iLora,
  type T2iMode,
} from '../types/t2i'

/**
 * Every value the dialog's inputs hold. The first eleven are the editable copy
 * an image keeps (T2iFormState, see toFormState); seed policy, Batch Size and
 * Count per Batch are session-local: actions, not properties of an image, so
 * loading an image or a draft never touches them.
 */
export interface T2iFields {
  mode: T2iMode
  filter: CaptionFilter
  caption: string
  model: string
  presetId: number | null
  megapixels: number
  aspect: string
  seed: number
  prompt: string
  negative: string
  loras: T2iLora[]
  seedPolicy: SeedPolicy
  batchSize: number
  countPerBatch: number
}

const PREFERRED_ASPECT = '3:2'

/** The workflow the config names for a model, if that preset is still registered. */
export function defaultPresetFor(
  cfg: T2iConfig | null,
  model: string,
  presetIds: number[],
): number | null {
  const id = cfg?.model_workflows[model] ?? null
  return id !== null && presetIds.includes(id) ? id : null
}

/** A fresh scratch form: the configured defaults and the given seed. */
export function initialFields(cfg: T2iConfig | null, presetIds: number[], seed: number): T2iFields {
  const models = cfg?.models ?? []
  const model = models.some((m) => m.id === cfg?.default_model)
    ? (cfg?.default_model ?? '')
    : (models[0]?.id ?? '')
  const aspects = cfg?.aspect_ratios ?? []
  return {
    mode: 'manual',
    filter: {},
    caption: '',
    model,
    presetId: defaultPresetFor(cfg, model, presetIds),
    megapixels: cfg?.default_megapixels ?? 1,
    aspect: aspects.includes(PREFERRED_ASPECT) ? PREFERRED_ASPECT : (aspects[0] ?? PREFERRED_ASPECT),
    seed,
    prompt: '',
    negative: '',
    loras: [],
    seedPolicy: 'fixed',
    batchSize: 1,
    countPerBatch: 1,
  }
}

/** The dialog's fields in the shape an image stores (and autosave / the draft send). */
export function toFormState(f: T2iFields): T2iFormState {
  return {
    mode: f.mode,
    filter: isFilterEmpty(f.filter) ? null : cleanFilter(f.filter),
    caption: f.caption,
    model: f.model,
    preset_id: f.presetId,
    megapixels: f.megapixels,
    aspect_ratio: f.aspect,
    seed: f.seed,
    prompt: f.prompt,
    negative: f.negative,
    loras: f.loras.map((l) => ({ name: l.name, strength: l.strength })),
  }
}

/**
 * Loads a stored form state into the fields and returns the result (the
 * inputs are not mutated). A key that is absent, and a null model, workflow,
 * size, aspect ratio or seed, leaves the current value alone: an image
 * ingested without one of them has nothing better to offer. A null text or
 * filter means an empty box. Seed policy, Batch Size and Count per Batch are
 * never touched, so loading an image can never queue a large re-run.
 */
export function mergeFormState(f: T2iFields, s: Partial<T2iFormState>): T2iFields {
  return {
    ...f,
    mode: s.mode ?? f.mode,
    filter: s.filter === undefined ? f.filter : s.filter === null ? {} : cleanFilter(s.filter),
    caption: s.caption === undefined ? f.caption : (s.caption ?? ''),
    model: s.model || f.model,
    presetId: s.preset_id ?? f.presetId,
    megapixels: s.megapixels ?? f.megapixels,
    aspect: s.aspect_ratio ?? f.aspect,
    seed: s.seed ?? f.seed,
    prompt: s.prompt === undefined ? f.prompt : (s.prompt ?? ''),
    negative: s.negative === undefined ? f.negative : (s.negative ?? ''),
    loras: s.loras === undefined ? f.loras : s.loras.map((l) => ({ name: l.name, strength: l.strength })),
  }
}

/**
 * The part of a stored draft the current setup can still honour: a model that
 * is no longer configured (with its workflow), a workflow that was deleted, an
 * aspect ratio the list no longer has, and Random mode without a caption file
 * are dropped. Returns a copy.
 */
export function usableDraft(
  draft: Partial<T2iFormState>,
  cfg: T2iConfig,
  presetIds: number[],
): Partial<T2iFormState> {
  const out = { ...draft }
  if (out.model && !cfg.models.some((m) => m.id === out.model)) {
    delete out.model
    delete out.preset_id
  }
  if (out.preset_id != null && !presetIds.includes(out.preset_id)) delete out.preset_id
  if (out.aspect_ratio && !cfg.aspect_ratios.includes(out.aspect_ratio)) delete out.aspect_ratio
  if (out.mode === 'random' && !cfg.csv.available) out.mode = 'manual'
  return out
}

/**
 * What autosave may send: the whole form, minus fields the server would
 * reject (an empty model, a size that is not positive, a seed outside
 * 0..2^31-1, a malformed ratio), so one bad field never blocks the rest.
 */
export function savableForm(s: T2iFormState): Partial<T2iFormState> {
  const out: Partial<T2iFormState> = { ...s }
  if (!s.model) delete out.model
  if (!(typeof s.megapixels === 'number' && s.megapixels > 0)) delete out.megapixels
  if (!(Number.isInteger(s.seed) && (s.seed as number) >= 0 && (s.seed as number) <= SEED_MAX)) delete out.seed
  if (typeof s.aspect_ratio === 'string' && !/^\d+:\d+$/.test(s.aspect_ratio)) delete out.aspect_ratio
  return out
}

export interface BatchContext {
  /** The selected model takes a negative prompt. */
  hasNegative: boolean
  maxBatchSize: number
  maxCount: number
}

/**
 * The POST /api/t2i/batches body for a Generate: one snapshot of the fields,
 * so nothing the user types afterwards can leak into a batch the server is
 * running. Manual sends its prompt, aspect ratio and (as provenance) its
 * caption. Random sends its filter, and its first batch is whatever the boxes
 * hold (see firstBatchSource): the prompt in the Prompt box, rendered as it
 * stands; else the caption in the Caption box, which has a prompt written for
 * it; else nothing, and the server rolls a caption. The batches after the first
 * always roll a caption and write a prompt. The counts are the EFFECTIVE ones:
 * Manual is locked at Batch Size 1, and Fixed forces Count per Batch to 1.
 */
export function buildBatchRequest(f: T2iFields, ctx: BatchContext): T2iBatchRequest {
  if (f.presetId === null) throw new Error('Choose a workflow first')
  const req: T2iBatchRequest = {
    mode: f.mode,
    model: f.model,
    preset_id: f.presetId,
    megapixels: f.megapixels,
    seed: f.seed,
    seed_policy: f.seedPolicy,
    batch_size: effectiveBatchSize(f.mode, f.batchSize, ctx.maxBatchSize),
    count_per_batch: effectiveCount(f.seedPolicy, f.countPerBatch, ctx.maxCount),
    loras: f.loras.map((l) => ({ name: l.name, strength: l.strength })),
  }
  if (f.mode === 'manual' || firstBatchSource(f) === 'prompt') {
    req.prompt = f.prompt
    req.aspect_ratio = f.aspect
    if (f.caption.trim()) req.caption = f.caption
    if (ctx.hasNegative && f.negative.trim()) req.negative = f.negative
  }
  if (f.mode === 'random') {
    req.filter = cleanFilter(f.filter)
    if (firstBatchSource(f) === 'caption') {
      req.caption = f.caption
      req.aspect_ratio = f.aspect
    }
  }
  return req
}

/** Where a Random Generate's first batch comes from. */
export type FirstBatchSource = 'prompt' | 'caption' | 'draw'

/**
 * What the boxes say batch 1 of a Random Generate is. The Prompt box is the
 * only thing that decides it, nothing remembered about how its text got there:
 *  - 'prompt'  the box holds text: it is rendered as it stands, with no caption
 *              rolled and no prompt written for it;
 *  - 'caption' the box is empty but the Caption box is not: a prompt is written
 *              for that caption, and no caption is rolled for it;
 *  - 'draw'    both are empty: a caption is rolled and a prompt written.
 * The batches after the first always roll a caption and write a prompt.
 */
export function firstBatchSource(f: T2iFields): FirstBatchSource {
  if (f.prompt.trim()) return 'prompt'
  return f.caption.trim() ? 'caption' : 'draw'
}

/**
 * Everything that makes a Manual image different from the last one, as one
 * comparable string, for the "nothing has changed" confirmation. The caption
 * is provenance only and is left out; so is a negative the model would not
 * receive. The seed policy and the effective count are in it, so switching
 * from one image to a run of several is not mistaken for a repeat.
 */
export function requestSignature(f: T2iFields, hasNegative: boolean): string {
  return JSON.stringify({
    prompt: f.prompt,
    negative: hasNegative ? f.negative : null,
    seed: f.seed,
    model: f.model,
    preset: f.presetId,
    megapixels: f.megapixels,
    aspect: f.aspect,
    loras: f.loras.map((l) => [l.name, l.strength]),
    policy: f.seedPolicy,
    count: effectiveCount(f.seedPolicy, f.countPerBatch),
  })
}

/**
 * When a Random batch ends its boxes unlock: Caption, Prompt and Aspect hold
 * the last step's values (and the negative, for a model that has one) and Seed
 * shows the next unused seed. Null when the batch was not Random or there is
 * nothing to hand back.
 */
export function unlockPatch(finished: T2iFinishedBatch, hasNegative: boolean): Partial<T2iFormState> | null {
  if (finished.mode !== 'random') return null
  const patch: Partial<T2iFormState> = {}
  const step = finished.step
  if (step) {
    patch.caption = step.caption
    patch.prompt = step.prompt
    if (step.aspect_ratio) patch.aspect_ratio = step.aspect_ratio
    if (hasNegative && step.negative !== null) patch.negative = step.negative
  }
  if (finished.next_seed !== null) patch.seed = finished.next_seed
  return Object.keys(patch).length > 0 ? patch : null
}

export interface BlockerContext {
  /** The dialog has loaded its settings and initialised the form. */
  ready: boolean
  csvAvailable: boolean
  randomRunning: boolean
  submitting: boolean
}

/** Why Generate cannot be pressed right now, in words for the user; null when it can. */
export function generateBlocker(f: T2iFields, ctx: BlockerContext): string | null {
  if (!ctx.ready) return 'Still loading'
  if (ctx.randomRunning) return 'A Random batch is running'
  if (ctx.submitting) return 'Submitting'
  if (!f.model) return 'Choose a model'
  if (f.presetId === null) return 'Choose a workflow'
  if (f.mode === 'random') return ctx.csvAvailable ? null : 'The caption file is not available'
  return f.prompt.trim() ? null : 'Write or generate a prompt first'
}
