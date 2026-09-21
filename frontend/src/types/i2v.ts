export interface I2vVideo {
  id: number
  source_path: string
  file_path: string
  prompt_used: string | null
  idea: string | null
  seed: number | null
  duration_s: number | null
  quality: string | null
  width: number | null
  height: number | null
  // Sampler steps actually applied: null for Fast renders and for a High
  // quality preset with no MS_STEPS node (its baked-in count was used).
  steps: number | null
  // Wall-clock render seconds; null for clips ingested before it existed.
  render_s: number | null
  preset_id: number | null
  comfy_prompt_id: string | null
  created_at: string
  is_favorite: boolean
}

export interface I2vConfig {
  fast_preset_id: number | null
  quality_preset_id: number | null
  durations: number[]
  default_duration: number
  default_quality: 'fast' | 'quality'
  megapixels: number[]
  default_megapixels: number
  steps: number[]
  default_steps: number
  // Clip placement: '' root = the default layout under comfy.output_root.
  output_root: string
  output_prefix: string
  // Whether the configured High quality preset binds MS_STEPS.
  quality_steps_supported: boolean
}

// One lint finding the server can rewrite without a model call.
export interface I2vFix {
  code: 'camera_phrase' | 'speech_format'
  message: string
  original: string
  replacement: string
}

export interface I2vLintReport {
  warnings: string[]
  fixes: I2vFix[]
  // The prompt with every fix applied; null when there is nothing to fix.
  fixed_prompt: string | null
}

export interface I2vJobChip {
  state: 'queued' | 'running' | 'failed'
  // A cancel request is in flight; the tile's button is disabled until
  // the job_update → cancelled event (or the request failing) settles it.
  cancelling?: boolean
  value?: number
  max?: number
  error?: string | null
}

/**
 * Display-only mirror of metascan/core/i2v_compiler.py::i2v_dims, so the
 * dialog can preview the output size before submitting. The backend stays
 * authoritative — it recomputes from the source's real dimensions. Mirrors
 * the VIDEO_TARGET_CAPS precedent in types/storyboard.ts; keep the two in
 * step if the Python changes.
 */
export const I2V_DIM_MULTIPLE = 32

export function i2vDims(
  srcW: number,
  srcH: number,
  megapixels: number,
  multiple: number = I2V_DIM_MULTIPLE,
): { width: number; height: number } | null {
  if (srcW <= 0 || srcH <= 0 || megapixels <= 0) return null
  const aspect = srcW / srcH
  const budget = megapixels * 1_000_000
  const snap = (v: number) => Math.max(multiple, Math.round(v / multiple) * multiple)
  const centre = Math.round(Math.sqrt(budget * aspect) / multiple)
  let best: { score: number; width: number; height: number } | null = null
  for (let step = -2; step <= 2; step++) {
    const width = Math.max(1, centre + step) * multiple
    const height = snap(width / aspect)
    const score =
      10 * Math.abs(Math.log(width / height / aspect)) +
      Math.abs(Math.log((width * height) / budget))
    if (!best || score < best.score) best = { score, width, height }
  }
  return best ? { width: best.width, height: best.height } : null
}

export const I2V_QUALITY_LABEL: Record<string, string> = {
  fast: 'Fast (turbo)',
  quality: 'High quality',
}

/** "47s", "4m 07s", "1h 02m" — compact wall-clock duration. */
export function formatRenderTime(seconds: number): string {
  const total = Math.max(0, Math.round(seconds))
  if (total < 60) return `${total}s`
  const pad = (n: number) => String(n).padStart(2, '0')
  if (total < 3600) return `${Math.floor(total / 60)}m ${pad(total % 60)}s`
  return `${Math.floor(total / 3600)}h ${pad(Math.floor((total % 3600) / 60))}m`
}

/**
 * i2v_videos.created_at is SQLite's datetime('now'): UTC, space-separated,
 * no zone marker. Parsed as-is a browser reads it as LOCAL time, so the
 * marker is added here before formatting into the viewer's locale.
 */
export function formatI2vTimestamp(raw: string | null | undefined): string {
  if (!raw) return ''
  const iso = /([zZ]|[+-]\d\d:?\d\d)$/.test(raw) ? raw : `${raw.replace(' ', 'T')}Z`
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return raw
  return d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

/** The three lines of the per-clip details label under each video tile. */
export function i2vVideoDetails(v: I2vVideo): {
  quality: string
  timing: string
  generated: string
} {
  const name = v.quality ? (I2V_QUALITY_LABEL[v.quality] ?? v.quality) : 'Unknown quality'
  const quality = v.steps ? `${name} · ${v.steps} steps` : name
  const length = v.duration_s != null ? `${v.duration_s}s clip` : 'clip'
  const timing =
    v.render_s != null ? `${length} · rendered in ${formatRenderTime(v.render_s)}` : length
  return { quality, timing, generated: formatI2vTimestamp(v.created_at) }
}
