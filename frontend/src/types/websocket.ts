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

// `taste` channel: see TasteWsEvent in ./taste. `scores_updated` means the
// score table changed (refetch GET /api/taste/scores); `training_*` mean the
// status changed (refetch GET /api/taste/status).

// `training` channel: `adapters_changed` with an empty payload whenever a
// prompt-writer adapter is renamed, deleted or changes status. Clients
// refetch GET /api/writers/adapters.
export type TrainingWsEvent = 'adapters_changed'
export type AdaptersChangedPayload = Record<string, never>
