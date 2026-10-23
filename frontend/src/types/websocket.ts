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
// prompt-writer adapter is renamed, deleted or changes status (clients
// refetch GET /api/writers/adapters); `gate_changed` when a training asset
// was downloaded / deleted (refetch GET /api/writers/training); the run
// lifecycle (`run_*`) and the hindsight briefs job (`briefs_*`), see
// types/training.ts and stores/training.ts::handleEvent.
export type TrainingWsEvent =
  | 'adapters_changed'
  | 'gate_changed'
  | import('./training').TrainingRunEvent
  | import('./training').BriefsEvent
export type AdaptersChangedPayload = Record<string, never>
