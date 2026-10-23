import { get } from './client'
import type { WriterAdapter } from '../types/writers'

/** Every prompt-writer adapter that is not deleted, in id order. */
export function listWriterAdapters(): Promise<WriterAdapter[]> {
  return get<WriterAdapter[]>('/writers/adapters')
}
