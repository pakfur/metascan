import { get, put } from './client'

export function fetchConfig(): Promise<Record<string, unknown>> {
  return get<Record<string, unknown>>('/config')
}

export function updateConfig(updates: Record<string, unknown>): Promise<Record<string, unknown>> {
  return put<Record<string, unknown>>('/config', updates)
}

export function fetchThemes(): Promise<{ themes: string[] }> {
  return get<{ themes: string[] }>('/config/themes')
}

export interface DirectoryListing {
  path: string
  // null at a filesystem root.
  parent: string | null
  dirs: { name: string; path: string }[]
}

// One level of the SERVER's directory tree (directories only). Omit the
// path to start at the server user's home directory.
export function browseDirectories(path?: string): Promise<DirectoryListing> {
  const q = path ? `?path=${encodeURIComponent(path)}` : ''
  return get<DirectoryListing>(`/config/browse${q}`)
}
