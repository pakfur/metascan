import type { T2iConfig } from '../types/t2i'

// Pure helpers for the Text to Image config tab (ConfigT2ITab.vue), kept out
// of the component so the parsing and the spread-merge can be reasoned about
// (and checked) without mounting anything.

export type T2iContentMode = T2iConfig['content_mode']

// [value, label] for the content-mode select, in display order. The values
// are exactly the backend's CONTENT_MODES.
export const CONTENT_MODE_OPTIONS: ReadonlyArray<readonly [T2iContentMode, string]> = [
  ['uncensored', 'Uncensored'],
  ['sfw', 'Safe for work'],
  ['default', 'Model default (no directive)'],
]

/** "0.5, 1, 1.5" -> [0.5, 1, 1.5]: positive finite numbers only, duplicates
 * dropped, in the order typed. Junk entries are skipped, not errors. */
export function parseMegapixels(text: string): number[] {
  const out: number[] = []
  for (const part of text.split(',')) {
    const trimmed = part.trim()
    if (!trimmed) continue
    const n = Number(trimmed)
    if (Number.isFinite(n) && n > 0 && !out.includes(n)) out.push(n)
  }
  return out
}

export interface PresetChoice {
  id: number
  label: string
}

/** Options for a "default workflow" select: the registered presets in order,
 * plus the current selection when it no longer exists (a deleted preset), so
 * the select never shows blank while quietly still holding a dangling id. */
export function presetChoices(
  presets: ReadonlyArray<{ id: number; name: string }>,
  currentId: number | null,
): PresetChoice[] {
  const out = presets.map((p) => ({ id: p.id, label: p.name }))
  if (currentId !== null && !presets.some((p) => p.id === currentId)) {
    out.push({ id: currentId, label: `#${currentId} (not found)` })
  }
  return out
}

export interface T2iFormValues {
  outputRoot: string
  outputPrefix: string
  /** Already parsed by parseMegapixels; the caller guarantees it is non-empty. */
  megapixels: number[]
  defaultMegapixels: number
  defaultModel: string
  contentMode: T2iContentMode
  /** model id -> default workflow preset id (null = none). */
  modelWorkflows: Record<string, number | null>
}

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

/** The raw `t2i` section of GET /api/config as an object: `{}` when it is
 * absent or malformed, so spreading it can never smear a string or array's
 * indices into the saved config. */
export function rawSection(v: unknown): Record<string, unknown> {
  return isPlainObject(v) ? v : {}
}

/** The `t2i` section to PUT. PUT /api/config is a shallow top-level merge, so
 * the raw section is spread first: keys this tab does not edit (window,
 * max_batch_size, max_count_per_batch, identity, ...) survive the save, and
 * so do per-model workflow entries for models the tab does not list. */
export function buildT2iSection(
  raw: Record<string, unknown>,
  f: T2iFormValues,
): Record<string, unknown> {
  return {
    ...raw,
    output_root: f.outputRoot.trim(),
    output_prefix: f.outputPrefix.trim(),
    megapixels: f.megapixels,
    default_megapixels: f.megapixels.includes(f.defaultMegapixels)
      ? f.defaultMegapixels
      : f.megapixels[0],
    default_model: f.defaultModel,
    content_mode: f.contentMode,
    model_workflows: {
      ...(isPlainObject(raw.model_workflows) ? raw.model_workflows : {}),
      ...f.modelWorkflows,
    },
  }
}
