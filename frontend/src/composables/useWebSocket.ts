import { ref, onUnmounted, getCurrentInstance } from 'vue'
import type { WsMessage, WsAction } from '../types/websocket'

type ChannelHandler = (event: string, data: Record<string, unknown>) => void

const handlers = new Map<string, Set<ChannelHandler>>()
const openHooks = new Set<() => void>()
let ws: WebSocket | null = null
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
const connected = ref(false)

function getWsUrl(): string {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${location.host}/ws`
}

function connect() {
  if (ws && ws.readyState <= WebSocket.OPEN) return

  ws = new WebSocket(getWsUrl())

  ws.onopen = () => {
    connected.value = true
    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    openHooks.forEach((h) => {
      try {
        h()
      } catch {
        // a hook's failure never breaks the connection
      }
    })
  }

  ws.onmessage = (ev) => {
    try {
      const msg: WsMessage = JSON.parse(ev.data)
      const channelHandlers = handlers.get(msg.channel)
      if (channelHandlers) {
        channelHandlers.forEach((h) => h(msg.event, msg.data))
      }
    } catch {
      // ignore malformed messages
    }
  }

  ws.onclose = () => {
    connected.value = false
    ws = null
    // Reconnect with backoff
    if (!reconnectTimer) {
      reconnectTimer = setTimeout(connect, 2000)
    }
  }

  ws.onerror = () => {
    ws?.close()
  }
}

export function sendAction(action: WsAction) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(action))
  }
}

/**
 * Run `hook` every time the socket (re)connects — after a network gap or a
 * server restart, events may have been missed, so refetch state here. Runs
 * at once when already connected. Channel handlers are unaffected. Removed
 * on unmount when called from a component; else call the returned stop().
 */
export function onWsConnect(hook: () => void): () => void {
  connect()
  openHooks.add(hook)
  if (connected.value) hook()
  const stop = () => {
    openHooks.delete(hook)
  }
  if (getCurrentInstance()) onUnmounted(stop)
  return stop
}

export function useWebSocket(channel: string, handler: ChannelHandler) {
  // Ensure connection is open
  connect()

  if (!handlers.has(channel)) {
    handlers.set(channel, new Set())
  }
  handlers.get(channel)!.add(handler)

  onUnmounted(() => {
    handlers.get(channel)?.delete(handler)
    if (handlers.get(channel)?.size === 0) {
      handlers.delete(channel)
    }
  })

  return { connected }
}
