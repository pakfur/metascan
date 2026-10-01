/**
 * Neutral types for the generation UI pieces in components/generation/ and
 * composables/useJobTracker.ts. T2I is the first user; I2V keeps its own
 * I2vJobChip until it is moved over. Nothing here knows about a feature.
 */

/** One ComfyUI job as a strip tile shows it: in flight, or failed and not yet dismissed. */
export interface JobChip {
  state: 'queued' | 'running' | 'failed'
  // A cancel request is in flight; the tile's button is disabled until the
  // job_update -> cancelled event (or the request failing) settles it.
  cancelling?: boolean
  // Sampler progress from the comfy channel's job_progress event.
  value?: number
  max?: number
  // The server's error text, shown verbatim on a failed tile.
  error?: string | null
}

/**
 * The tile's headline: "cancelling…", a percentage once the sampler reports
 * progress, otherwise the bare state word. Progress numbers that do not add
 * up (NaN, max of 0) fall back to the state word rather than showing "NaN%".
 */
export function jobChipLabel(chip: JobChip): string {
  if (chip.cancelling) return 'cancelling…'
  if (
    chip.state === 'running' &&
    typeof chip.value === 'number' &&
    typeof chip.max === 'number' &&
    Number.isFinite(chip.value) &&
    Number.isFinite(chip.max) &&
    chip.max > 0
  ) {
    const pct = Math.round((chip.value / chip.max) * 100)
    return `${Math.min(100, Math.max(0, pct))}%`
  }
  return chip.state
}

/** One image tile in a ThumbGrid; the caller builds `title` (tooltip) and `label`. */
export interface ThumbItem {
  id: number | string
  file_path: string
  is_favorite: boolean
  title: string
  label: string
}
