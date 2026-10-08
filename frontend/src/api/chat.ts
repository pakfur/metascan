import { ApiError } from './client'

export type ChatRole = 'user' | 'assistant'

export interface ChatMessageIn {
  role: ChatRole
  content: string
}

export interface ChatRequest {
  messages: ChatMessageIn[]
  system_prompt: string
  file_path: string | null
  include_image: boolean
  temperature: number
  max_tokens: number
}

export type ChatEvent =
  | { type: 'delta'; text: string }
  | { type: 'reasoning'; text: string }
  | { type: 'done'; elapsed_ms: number; vlm_model_id: string }
  | { type: 'error'; message: string }

/**
 * POST /api/chat and invoke `onEvent` for every NDJSON line the server
 * streams back. Resolves when the stream ends; rejects with ApiError for
 * pre-stream HTTP failures (503 VLM not ready, 404, 422) and with an
 * AbortError DOMException when `signal` fires.
 *
 * Uses fetch directly (not the `request<T>` wrapper in client.ts) because
 * that wrapper buffers the whole body through `res.json()`.
 */
export async function streamChat(
  body: ChatRequest,
  onEvent: (ev: ChatEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  const apiKey = localStorage.getItem('metascan_api_key')
  if (apiKey) headers['Authorization'] = `Bearer ${apiKey}`

  const res = await fetch('/api/chat', {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
    signal,
  })
  if (!res.ok) {
    const payload = await res.json().catch(() => ({ detail: res.statusText }))
    const detail = (payload as { detail?: unknown }).detail
    const msg =
      typeof detail === 'string'
        ? detail
        : Array.isArray(detail)
          ? detail.map((d) => (d as { msg?: string }).msg ?? '').join('; ')
          : `HTTP ${res.status}`
    throw new ApiError(res.status, detail, msg)
  }
  if (!res.body) throw new Error('empty response body')

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  const flushLine = (line: string) => {
    const trimmed = line.trim()
    if (!trimmed) return
    try {
      onEvent(JSON.parse(trimmed) as ChatEvent)
    } catch {
      /* ignore malformed line */
    }
  }
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    let nl: number
    while ((nl = buf.indexOf('\n')) >= 0) {
      flushLine(buf.slice(0, nl))
      buf = buf.slice(nl + 1)
    }
  }
  buf += decoder.decode()
  flushLine(buf)
}
