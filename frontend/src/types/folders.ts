// Rule fields are driven by what Media actually exposes (see types/media.ts),
// not the prototype's fake-data superset. Anything referenced here must be
// trivially derivable from a Media summary + detail record.
export type RuleField =
  | 'favorite'
  | 'type'
  | 'model'
  | 'filename'
  | 'tags'
  | 'modified'
  | 'added'

export type RuleOp =
  | 'is'
  | 'is_not'
  | 'contains'
  | 'does_not_contain'
  | 'starts_with'
  | 'all_of'
  | 'any_of'
  | 'within_days'
  | 'older_than_days'

// Ruleset is a superset — per-field only a subset of `ops` + `value` shapes
// are meaningful; see FIELD_DEFS in stores/folders.ts for the allow-list.
export type RuleValue = string | number | boolean | string[]

export interface SmartCondition {
  field: RuleField
  op: RuleOp
  value: RuleValue
}

export interface SmartRules {
  match: 'all' | 'any'
  conditions: SmartCondition[]
}

export interface ManualFolder {
  id: string
  name: string
  kind: 'manual'
  icon: string
  // Stable identifier for media is `file_path`; a path can appear at most once.
  items: string[]
  createdAt: number
}

export interface SmartFolder {
  id: string
  name: string
  kind: 'smart'
  icon: string
  rules: SmartRules
  createdAt: number
}

export type AnyFolder = ManualFolder | SmartFolder

export type FolderScope =
  | { kind: 'library' }
  | { kind: 'manual'; id: string }
  | { kind: 'smart'; id: string }
