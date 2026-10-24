import { ApiError } from '../api/client'

// While a writer LoRA trains, every GPU consumer route answers 409
// {code: "gpu_busy_training"}. Show this text, not a generic failure.
export const GPU_BUSY_TRAINING = 'gpu_busy_training'
export const GPU_BUSY_TEXT = 'GPU busy: training'

export function isGpuBusyTraining(e: unknown): boolean {
  if (!(e instanceof ApiError) || e.status !== 409) return false
  const code = (e.detail as { code?: unknown } | null)?.code
  return code === GPU_BUSY_TRAINING
}

/** GPU_BUSY_TEXT for a training refusal, else the error's message. */
export function errorText(e: unknown): string {
  if (isGpuBusyTraining(e)) return GPU_BUSY_TEXT
  return e instanceof Error ? e.message : String(e)
}
