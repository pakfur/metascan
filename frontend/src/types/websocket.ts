export interface WsMessage {
  channel: string
  event: string
  data: Record<string, unknown>
}

export interface WsAction {
  action: string
  data?: Record<string, unknown>
}
