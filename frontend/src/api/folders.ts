import { get, post, patch, del } from './client'
import type { AnyFolder, SmartRules } from '../types/folders'

/**
 * Wire shape returned by the backend. Matches `AnyFolder` closely — the
 * two differences are the server-materialized `count` and `updated_at`
 * plus a `sort_order` field we don't yet surface in the UI.
 */
export interface FolderRecord {
  id: string
  kind: 'manual' | 'smart'
  name: string
  icon: string
  sort_order: number
  created_at: number
  updated_at: number
  count: number
  items?: string[]
  rules?: SmartRules
}

export function fetchFolders(): Promise<FolderRecord[]> {
  return get<FolderRecord[]>('/folders')
}

export function createFolder(body: {
  id: string
  kind: 'manual' | 'smart'
  name: string
  icon?: string
  rules?: SmartRules
  items?: string[]
  sort_order?: number
}): Promise<FolderRecord> {
  return post<FolderRecord>('/folders', body)
}

export function updateFolder(
  id: string,
  body: Partial<Pick<AnyFolder, 'name' | 'icon'>> & {
    rules?: SmartRules
    sort_order?: number
  },
): Promise<FolderRecord> {
  return patch<FolderRecord>(`/folders/${encodeURIComponent(id)}`, body)
}

export function deleteFolder(id: string): Promise<{ status: string }> {
  return del<{ status: string }>(`/folders/${encodeURIComponent(id)}`)
}

export function addFolderItems(
  id: string,
  paths: string[],
): Promise<{ added: number }> {
  return post<{ added: number }>(
    `/folders/${encodeURIComponent(id)}/items`,
    { paths },
  )
}

export function removeFolderItems(
  id: string,
  paths: string[],
): Promise<{ removed: number }> {
  return del<{ removed: number }>(
    `/folders/${encodeURIComponent(id)}/items`,
    { paths },
  )
}
