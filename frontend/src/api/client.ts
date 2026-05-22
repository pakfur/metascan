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

export function thumbnailUrl(filePath: string): string {
  return `${API_BASE}/thumbnails/${encodeURIComponent(filePath)}`
}

export function streamUrl(filePath: string): string {
  return `${API_BASE}/stream/${encodeURIComponent(filePath)}`
}
