import { shallowReactive } from 'vue'

const API_BASE = '/api'

export class ApiError extends Error {
  status: number
  detail: unknown
  constructor(status: number, detail: unknown, message: string) {
    super(message)
    this.status = status
    this.detail = detail
  }
}

function errorMessage(detail: unknown, fallback: string): string {
  if (typeof detail === 'string') return detail
  if (detail && typeof detail === 'object') {
    const msg = (detail as { message?: unknown }).message
    if (typeof msg === 'string') return msg
  }
  return fallback
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...((options.headers as Record<string, string>) || {}),
  }

  const apiKey = localStorage.getItem('metascan_api_key')
  if (apiKey) {
    headers['Authorization'] = `Bearer ${apiKey}`
  }

  const res = await fetch(`${API_BASE}${path}`, { ...options, headers })
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }))
    const detail = (body as { detail?: unknown }).detail
    throw new ApiError(res.status, detail, errorMessage(detail, `HTTP ${res.status}`))
  }
  return res.json()
}

export function get<T>(path: string): Promise<T> {
  return request<T>(path)
}

export function post<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  return request<T>(path, {
    method: 'POST',
    body: body !== undefined ? JSON.stringify(body) : undefined,
    signal,
  })
}

export function put<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, {
    method: 'PUT',
    body: JSON.stringify(body),
  })
}

export function patch<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, {
    method: 'PATCH',
    body: JSON.stringify(body),
  })
}

export function del<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, {
    method: 'DELETE',
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
}

function bearerHeaders(): Record<string, string> {
  const apiKey = localStorage.getItem('metascan_api_key')
  return apiKey ? { Authorization: `Bearer ${apiKey}` } : {}
}

async function failure(res: Response): Promise<ApiError> {
  const body = await res.json().catch(() => ({ detail: res.statusText }))
  const detail = (body as { detail?: unknown }).detail
  return new ApiError(res.status, detail, errorMessage(detail, `HTTP ${res.status}`))
}

/** Sends a file as the raw request body. */
export async function uploadFile<T>(path: string, file: File): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/octet-stream', ...bearerHeaders() },
    body: file,
  })
  if (!res.ok) throw await failure(res)
  return res.json()
}

/** Fetches a file and hands it to the browser's save flow. */
export async function downloadFile(path: string, fallbackName: string): Promise<string> {
  const res = await fetch(`${API_BASE}${path}`, { headers: bearerHeaders() })
  if (!res.ok) throw await failure(res)
  const named = /filename="?([^";]+)"?/.exec(res.headers.get('Content-Disposition') ?? '')
  const name = named?.[1] ?? fallbackName
  const url = URL.createObjectURL(await res.blob())
  const link = document.createElement('a')
  link.href = url
  link.download = name
  link.click()
  URL.revokeObjectURL(url)
  return name
}

// An upscale or revert puts new bytes behind the same URL. The server's ETag
// keeps a reload right, but an element already on screen never asks again, so
// the URL itself changes for the rest of this session.
const fileVersions = shallowReactive(new Map<string, number>())

export function bumpFileVersion(filePath: string): void {
  fileVersions.set(filePath, (fileVersions.get(filePath) ?? 0) + 1)
}

function versioned(url: string, filePath: string): string {
  const version = fileVersions.get(filePath)
  return version ? `${url}?v=${version}` : url
}

export function thumbnailUrl(filePath: string): string {
  return versioned(`${API_BASE}/thumbnails/${encodeURIComponent(filePath)}`, filePath)
}

export function streamUrl(filePath: string): string {
  return versioned(`${API_BASE}/stream/${encodeURIComponent(filePath)}`, filePath)
}
