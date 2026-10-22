export interface WsMessage {
  channel: string
  event: string
  data: Record<string, unknown>
}

export interface WsAction {
  action: string
  data?: Record<string, unknown>
}

// `categories` channel: `categories_changed` with an empty payload on every
// category create / rename / delete / membership change. Clients refetch
// GET /api/categories.
export type CategoriesWsEvent = 'categories_changed'
export type CategoriesChangedPayload = Record<string, never>
