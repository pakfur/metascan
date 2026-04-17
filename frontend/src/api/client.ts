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

export interface FetchPhases {
  ttfb: number       // dispatch -> response headers received
  body: number       // headers received -> full body buffered
  parse: number      // body received -> JSON.parse complete
  bytes: number      // response body size
  // Browser-reported phases (from PerformanceResourceTiming). These are
  // the most trustworthy because they're measured by the browser itself,
  // not by user-space JS which can be blocked on the main thread.
  // Undefined when the browser didn't emit an entry (e.g. opaque CORS).
  queued?: number    // fetch() call -> browser's fetchStart (pool/queue)
  connect?: number   // TCP + TLS handshake (0 on reused connection)
  send?: number      // connectEnd -> requestStart (usually ~0)
  waiting?: number   // requestStart -> responseStart (server round-trip)
  download?: number  // responseStart -> responseEnd (bytes on wire)
}

function resourceTimingFor(url: string): PerformanceResourceTiming | null {
  try {
    const absolute = new URL(url, window.location.origin).toString()
    const entries = performance.getEntriesByName(absolute, 'resource')
    const entry = entries[entries.length - 1] as PerformanceResourceTiming | undefined
    return entry ?? null
  } catch {
    return null
  }
}

// Performs the request and returns both the parsed body AND a phase
// breakdown. Use `request` for callers that don't need timing — it
// delegates here and discards phases.
async function requestWithPhases<T>(
  path: string,
  options: RequestInit = {},
): Promise<{ data: T; phases: FetchPhases }> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...((options.headers as Record<string, string>) || {}),
  }

  const apiKey = localStorage.getItem('metascan_api_key')
  if (apiKey) {
    headers['Authorization'] = `Bearer ${apiKey}`
  }

  const url = `${API_BASE}${path}`
  const t0 = performance.now()
  const res = await fetch(url, { ...options, headers })
  const tHead = performance.now()
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }))
    const detail = (body as { detail?: unknown }).detail
    throw new ApiError(res.status, detail, errorMessage(detail, `HTTP ${res.status}`))
  }
  // Read the body as text first so we can measure body-transfer vs parse
  // separately — response.json() bundles them. JSON.parse on a string is
  // fast; slow body phase implicates the network/proxy, slow parse phase
  // implicates a giant payload or a busy main thread.
  const text = await res.text()
  const tBody = performance.now()
  const data = text ? JSON.parse(text) : (null as unknown as T)
  const tParse = performance.now()

  const phases: FetchPhases = {
    ttfb: tHead - t0,
    body: tBody - tHead,
    parse: tParse - tBody,
    bytes: text.length,
  }
  const entry = resourceTimingFor(url)
  if (entry) {
    // The PerformanceResourceTiming clock and performance.now() share the
    // same time origin, so we can subtract directly. `queued` is the gap
    // between our `fetch()` call and the browser actually starting to
    // work on the request — a large value means the browser held the
    // request in its connection pool (6-per-origin HTTP/1.1 cap) or had
    // nothing to send yet.
    phases.queued = Math.max(0, entry.fetchStart - t0)
    phases.connect = Math.max(0, entry.connectEnd - entry.connectStart)
    phases.send = Math.max(0, entry.requestStart - entry.connectEnd)
    phases.waiting = Math.max(0, entry.responseStart - entry.requestStart)
    phases.download = Math.max(0, entry.responseEnd - entry.responseStart)
  }
  return { data: data as T, phases }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const { data } = await requestWithPhases<T>(path, options)
  return data
}

export function get<T>(path: string): Promise<T> {
  return request<T>(path)
}

// Same as get() but also returns a FetchPhases breakdown so callers can
// log where time is spent (network vs body transfer vs parse).
export function getWithPhases<T>(
  path: string,
): Promise<{ data: T; phases: FetchPhases }> {
  return requestWithPhases<T>(path)
}

export function post<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, {
    method: 'POST',
    body: body !== undefined ? JSON.stringify(body) : undefined,
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

export function del<T>(path: string): Promise<T> {
  return request<T>(path, { method: 'DELETE' })
}

export function thumbnailUrl(filePath: string): string {
  return `${API_BASE}/thumbnails/${encodeURIComponent(filePath)}`
}

export function streamUrl(filePath: string): string {
  return `${API_BASE}/stream/${encodeURIComponent(filePath)}`
}
