import type { Media } from './media'

export interface UpscaleWorkflow {
  id: number
  name: string
  // What the graph can take; the dialog offers only these.
  has_seed: boolean
  has_prompt: boolean
  has_scale: boolean
  has_negative: boolean
  has_loras: boolean
  has_denoise: boolean
  has_model: boolean
  // MS_STEPS: the sampler's step count, and the count the graph has baked in.
  has_steps: boolean
  steps: number | null
  // MS_MAX_SIZE: a cap on the output's long edge.
  has_max_size: boolean
  // The model the graph loads (MS_MODEL), null when it has none.
  model: string | null
  // The T2I model profile a prompt for this graph is written in ('moss'),
  // and its label; null when the dialog cannot write one from the image.
  prompt_model: string | null
  prompt_model_label: string | null
}

// POST /upscale/prompt-from-image: the image captioned, then rewritten in
// the workflow's prompt style.
export interface PromptFromImage {
  caption: string
  caption_model: string
  prompt: string
  negative: string | null
  warnings: string[]
  writer_id: string | null
  writer_model: string | null
}

export interface UpscaleLora {
  name: string
  strength: number
}

// What the item was generated with (GET /upscale/state), the dialog's
// pre-fill. A blind scorecard image (no grade yet) gives no prompt.
export interface UpscaleGeneration {
  prompt: string | null
  negative: string | null
  seed: number | null
  model: string | null
  loras: UpscaleLora[]
  blind: boolean
  // The refine prompt for the image's subject, guessed from its tags.
  refine_prompt: string | null
}

// A short prompt about the detail to add, for a refine workflow at a low
// denoise (data/upscale_refine_prompts.yml, else built in).
export interface RefinePrompt {
  id: string
  label: string
  prompt: string
  negative: string
  subject: 'people' | 'scene' | 'illustration' | null
}

// Where the Prompt box's text came from: the image's own prompt, one
// written from the image, or a refine prompt's id.
export type PromptSource = 'image' | 'written' | string

// The source a refine-capable dialog starts on: a refine prompt at or below
// the configured denoise (a workflow without MS_DENOISE never), else the
// image's own prompt; a refine prompt too when the image has none.
export function defaultPromptSource(opts: {
  refinePrompts: RefinePrompt[]
  suggested: string | null
  imagePrompt: string | null
  hasDenoise: boolean
  denoise: number | null
  maxDenoise: number
}): PromptSource {
  const { refinePrompts, suggested, imagePrompt, hasDenoise, denoise, maxDenoise } = opts
  const refine =
    refinePrompts.find((p) => p.id === suggested)?.id ?? refinePrompts[0]?.id ?? null
  if (refine === null) return 'image'
  const low = hasDenoise && denoise !== null && denoise <= maxDenoise + 1e-9
  if (low || !imagePrompt) return refine
  return 'image'
}

export interface UpscaleConfig {
  original_root: string
  resolved_original_root: string
  default_image_preset_id: number | null
  default_video_preset_id: number | null
  max_count: number
  window: number
  scales: number[]
  default_scale: number
  denoise_levels: number[]
  default_denoise: number
  // At or below this denoise the dialog starts on a refine prompt.
  refine_prompt_max_denoise: number
  // Longest output edges offered with MS_MAX_SIZE; 0 means no limit.
  max_sizes: number[]
  default_max_size: number
  image_workflows: UpscaleWorkflow[]
  video_workflows: UpscaleWorkflow[]
  refine_prompts: RefinePrompt[]
}

export interface UpscaleRecord {
  original_path: string
  original_name: string
  preset_id: number | null
  upscaled_at: string
}

export interface UpscaleCandidate {
  id: number
  source_path: string
  file_path: string
  kind: 'upscale_image' | 'upscale_video'
  preset_id: number | null
  preset_name: string | null
  seed: number | null
  prompt: string | null
  scale: number | null
  denoise: number | null
  width: number | null
  height: number | null
  duration_s: number | null
  file_size: number | null
  render_s: number | null
  audio_lost: boolean
  job_id: number | null
  created_at: string
}

export interface UpscaleState {
  record: UpscaleRecord | null
  candidates: UpscaleCandidate[]
  generation?: UpscaleGeneration | null
}

export interface UpscaleRun {
  batch_id: string
  source_path: string
  phase: string
  queued: number
  ended: number
  total: number
  error?: string
}

export interface UpscaleRunBody {
  source_path: string
  preset_id: number
  count: number
  seed: number | null
  seed_policy: 'fixed' | 'increment' | 'decrement' | 'random'
  prompt: string | null
  scale: number | null
  negative: string | null
  // An MS_LORA_STACK node gets exactly this list; null leaves it empty.
  loras: UpscaleLora[] | null
  denoise: number | null
  model: string | null
  steps: number | null
  max_size: number | null
}

export function candidateLabel(c: UpscaleCandidate): string {
  const size = c.width && c.height ? `${c.width}×${c.height}` : ''
  return [
    size,
    c.scale ? `×${c.scale}` : '',
    c.denoise != null ? `d ${c.denoise}` : '',
  ]
    .filter(Boolean)
    .join(' · ')
}

function family(name: string | null): string | null {
  if (!name) return null
  const parts = name.replace(/\\/g, '/').split('/').filter(Boolean)
  return parts.length > 1 ? parts[0].toLowerCase() : null
}

// True when a refine graph that loads `workflowModel` can take an image made
// with `imageModel`: the same file, or one from the same model folder (same
// text encoder, VAE and LoRAs). Mirrors upscale_defaults.same_family.
export function sameModelFamily(
  imageModel: string | null,
  workflowModel: string | null,
): boolean {
  if (!imageModel || !workflowModel) return false
  const a = imageModel.replace(/\\/g, '/').toLowerCase()
  const b = workflowModel.replace(/\\/g, '/').toLowerCase()
  if (a === b) return true
  const fa = family(imageModel)
  return fa !== null && fa === family(workflowModel)
}

export function workflowsFor(config: UpscaleConfig, media: Media): UpscaleWorkflow[] {
  return media.is_video ? config.video_workflows : config.image_workflows
}

export function defaultWorkflowId(config: UpscaleConfig, media: Media): number | null {
  const list = workflowsFor(config, media)
  const wanted = media.is_video
    ? config.default_video_preset_id
    : config.default_image_preset_id
  return list.find((w) => w.id === wanted)?.id ?? list[0]?.id ?? null
}

// A placeholder for a result whose job ComfyUI does not have yet: the
// source is still uploading, or the run waits for a free slot (a run keeps
// `window` jobs in ComfyUI at a time). A submitted job gets its own tile.
export interface WaitingTile {
  key: string
  batchId: string
  label: string
  hint: string
}

export function waitingTiles(runs: UpscaleRun[], window: number): WaitingTile[] {
  const tiles: WaitingTile[] = []
  for (const run of runs) {
    const left = Math.max(0, run.total - run.queued)
    for (let i = 0; i < left; i++) {
      const uploading = run.phase === 'uploading' && i === 0
      tiles.push({
        key: `${run.batch_id}-${run.queued + i}`,
        batchId: run.batch_id,
        label: uploading ? 'uploading' : 'waiting',
        hint: uploading
          ? 'Sending the source to ComfyUI'
          : run.phase === 'uploading'
            ? 'Starts once the source is in ComfyUI'
            : `Starts when a slot is free: ComfyUI gets ${window} of a run at a time`,
      })
    }
  }
  return tiles
}
