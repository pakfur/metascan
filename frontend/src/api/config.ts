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
