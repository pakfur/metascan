import { del, downloadFile, get, post, uploadFile } from './client'

const BASE = '/library-transfer'

export interface TransferRoot {
  source: string
  target: string | null
  paths: number
  found: number
}

export interface ImportPreview {
  token: string
  created_at: string | null
  counts: Record<string, number>
  roots: TransferRoot[]
  outside: { paths: number; found: number }
  generated_to_ingest: number
  targets: string[]
}

export interface ImportCount {
  added: number
  already: number
  not_found: number
}

export interface ImportReport {
  counts: Record<string, ImportCount>
  ingested: number
  failed: number
}

export interface ImportStatus {
  running: boolean
  phase?: 'preparing' | 'ingesting' | 'applying' | null
  done?: number
  total?: number
  report?: ImportReport | null
  error?: string | null
}

export type RootMapping = Record<string, string | null>

export function exportLibraryData(): Promise<string> {
  return downloadFile(`${BASE}/export`, 'metascan-library-data.zip')
}

export function uploadBundle(file: File): Promise<ImportPreview> {
  return uploadFile<ImportPreview>(`${BASE}/import/upload`, file)
}

export function previewImport(token: string, mapping: RootMapping): Promise<ImportPreview> {
  return post<ImportPreview>(`${BASE}/import/preview`, { token, mapping })
}

export function applyImport(token: string, mapping: RootMapping): Promise<{ status: string }> {
  return post<{ status: string }>(`${BASE}/import/apply`, { token, mapping })
}

export function fetchImportStatus(): Promise<ImportStatus> {
  return get<ImportStatus>(`${BASE}/import/status`)
}

export function discardImport(token: string): Promise<{ status: string }> {
  return del<{ status: string }>(`${BASE}/import/${token}`)
}
