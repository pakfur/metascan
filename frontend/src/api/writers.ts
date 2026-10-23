import { del, get, patch } from './client'
import type { WriterAdapter } from '../types/writers'

/** Every prompt-writer adapter that is not deleted, in id order. */
export function listWriterAdapters(): Promise<WriterAdapter[]> {
  return get<WriterAdapter[]>('/writers/adapters')
}

/** Rename an adapter (409 duplicate_name, 422 invalid_name). */
export function renameWriterAdapter(id: number, name: string): Promise<WriterAdapter> {
  return patch<WriterAdapter>(`/writers/adapters/${id}`, { name })
}

/**
 * Delete an adapter: its files and row (history keeps the name). 409
 * adapter_training while it trains, adapter_files_busy while the writer still
 * maps its file. A paused / failed adapter's open run is discarded with it.
 */
export function deleteWriterAdapter(id: number): Promise<{ status: string }> {
  return del<{ status: string }>(`/writers/adapters/${id}`)
}
