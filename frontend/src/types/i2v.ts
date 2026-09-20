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
}

export interface I2vJobChip {
  state: 'queued' | 'running' | 'failed'
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
