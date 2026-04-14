import { post } from './client'

export interface ScanPrepareResult {
  directories: { path: string; file_count: number; search_subfolders: boolean }[]
  total_files: number
  existing_in_db: number
}

export function prepareScan(): Promise<ScanPrepareResult> {
  return post<ScanPrepareResult>('/scan/prepare')
}

export function startScan(fullCleanup = false): Promise<{ status: string }> {
  return post<{ status: string }>('/scan/start', { full_cleanup: fullCleanup })
}

export function cancelScan(): Promise<{ status: string }> {
  return post<{ status: string }>('/scan/cancel')
}
