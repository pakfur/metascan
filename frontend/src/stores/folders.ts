import { defineStore } from 'pinia'
import { ref, computed, watch } from 'vue'
import type { Media } from '../types/media'
import type {
  AnyFolder,
  FolderScope,
  ManualFolder,
  RuleField,
  RuleOp,
  SmartCondition,
  SmartFolder,
  SmartRules,
} from '../types/folders'
import { fileName } from '../utils/path'
import { fetchTagPaths } from '../api/filters'
import * as foldersApi from '../api/folders'
import type { FolderRecord } from '../api/folders'

const LEGACY_STORAGE_KEY = 'metascan.folders.v1'
const MIGRATION_FLAG_KEY = 'metascan.folders.v1.imported'

interface PersistedState {
  manual: ManualFolder[]
  smart: SmartFolder[]
}

function randId(prefix: string): string {
  return prefix + '_' + Math.random().toString(36).slice(2, 8)
}

// Migrate persisted smart folders in two ways:
//   1. Legacy tag operators ('contains' / 'contains_any' / 'does_not_contain')
//      get remapped to the current 'all_of' / 'any_of' vocabulary.
//   2. Conditions whose field is no longer supported ('prompt', 'dimensions')
//      are dropped — those rows would otherwise show as blank selects in the
//      editor and the evaluator would silently reject their media.
const DROPPED_FIELDS = new Set(['prompt', 'dimensions'])

function migrateSmartFolder(s: SmartFolder): SmartFolder {
  let dirty = false
  const conditions: SmartCondition[] = []
  for (const c of s.rules.conditions) {
    if (DROPPED_FIELDS.has(c.field as string)) {
      dirty = true
      continue
    }
    if (c.field !== 'tags') {
      conditions.push(c)
      continue
    }
    if (c.op === 'contains' || c.op === 'does_not_contain') {
      dirty = true
      conditions.push({ ...c, op: 'all_of' as RuleOp })
      continue
    }
    if ((c.op as RuleOp) === ('contains_any' as RuleOp)) {
      dirty = true
      conditions.push({ ...c, op: 'any_of' as RuleOp })
      continue
    }
    conditions.push(c)
  }
  if (!dirty) return s
  if (conditions.length === 0) {
    conditions.push({ field: 'favorite', op: 'is', value: true })
  }
  return { ...s, rules: { ...s.rules, conditions } }
}

function readLegacyLocalStorage(): PersistedState {
  try {
    const raw = localStorage.getItem(LEGACY_STORAGE_KEY)
    if (!raw) return { manual: [], smart: [] }
    const parsed = JSON.parse(raw) as PersistedState
    const smart = Array.isArray(parsed.smart)
      ? parsed.smart.map(migrateSmartFolder)
      : []
    return {
      manual: Array.isArray(parsed.manual) ? parsed.manual : [],
      smart,
    }
  } catch {
    return { manual: [], smart: [] }
  }
}

// Server record → client type. Server stores `created_at` as unix seconds;
// the client type has `createdAt` in ms. Icon + name come across verbatim.
function recordToManual(r: FolderRecord): ManualFolder {
  return {
    id: r.id,
    name: r.name,
    kind: 'manual',
    icon: r.icon,
    items: r.items ?? [],
    createdAt: Math.round((r.created_at ?? 0) * 1000),
  }
}

function recordToSmart(r: FolderRecord): SmartFolder {
  return {
    id: r.id,
    name: r.name,
    kind: 'smart',
    icon: r.icon,
    rules: r.rules ?? { match: 'all', conditions: [] },
    createdAt: Math.round((r.created_at ?? 0) * 1000),
  }
}

// Module-level tag→paths cache. Populated by the store's loadTagPaths(), read
// by the synchronous evaluateCondition(). Using a module-scoped map (rather
// than threading context through every call) keeps matches()/evaluator
// call sites unchanged.
let tagPathSets: Record<string, Set<string>> = {}

function normalizeModel(m: Media): string {
  if (Array.isArray(m.model) && m.model.length > 0) return m.model[0]
  return ''
}

export function evaluateCondition(m: Media, c: SmartCondition): boolean {
  const { field, op, value } = c
  switch (field) {
    case 'favorite': {
      const v = Boolean(value)
      return op === 'is' ? m.is_favorite === v : m.is_favorite !== v
    }
    case 'type': {
      const kind = m.is_video ? 'video' : 'image'
      return op === 'is' ? kind === value : kind !== value
    }
    case 'model': {
      const model = normalizeModel(m)
      const v = String(value ?? '')
      if (op === 'is') return model === v
      if (op === 'is_not') return model !== v
      if (op === 'contains')
        return model.toLowerCase().includes(v.toLowerCase())
      return false
    }
    case 'filename': {
      const hay = (m.file_name ?? fileName(m.file_path)).toLowerCase()
      const needle = String(value ?? '').toLowerCase()
      if (op === 'contains') return hay.includes(needle)
      if (op === 'does_not_contain') return !hay.includes(needle)
      if (op === 'starts_with') return hay.startsWith(needle)
      return false
    }
    case 'tags': {
      // Tag membership comes from the same inverted index the sidebar tag
      // filter uses. We can't rely on m.tags — it's only populated on
      // detail-loaded records.
      const vals = Array.isArray(value)
        ? value
        : value === undefined || value === null || value === ''
          ? []
          : [String(value)]
      if (vals.length === 0) return false
      const path = m.file_path
      const hasTag = (v: string): boolean => tagPathSets[v]?.has(path) ?? false
      if (op === 'all_of') return vals.every(hasTag)
      if (op === 'any_of') return vals.some(hasTag)
      return false
    }
    case 'modified':
    case 'added': {
      // 'modified' reads the file's mtime; 'added' reads the row's
      // created_at. Two historical value shapes: ISO timestamp string
      // (new rows) or stringified unix-epoch float (back-filled rows).
      const raw = field === 'modified' ? m.modified_at : m.created_at
      if (!raw) return false
      let t: number
      const asNum = Number(raw)
      if (Number.isFinite(asNum)) {
        t = asNum * 1000
      } else {
        t = Date.parse(raw)
      }
      if (Number.isNaN(t)) return false
      const days = Number(value) || 0
      const cutoff = Date.now() - days * 86400000
      if (op === 'within_days') return t >= cutoff
      if (op === 'older_than_days') return t < cutoff
      return false
    }
  }
}

export function matches(m: Media, rules: SmartRules): boolean {
  if (!rules.conditions.length) return true
  const results = rules.conditions.map((c) => evaluateCondition(m, c))
  return rules.match === 'any' ? results.some(Boolean) : results.every(Boolean)
}

export const useFoldersStore = defineStore('folders', () => {
  const manualFolders = ref<ManualFolder[]>([])
  const smartFolders = ref<SmartFolder[]>([])
  const scope = ref<FolderScope>({ kind: 'library' })
  const loading = ref(false)
  // Monotonic counter bumped whenever the tag→paths cache is refreshed. Any
  // computed that depends on tag-based smart-folder membership reads this so
  // Vue knows to recompute after an async reload.
  const tagPathsVersion = ref(0)
  let cachedTagKeys = new Set<string>()
  let inflightTagFetch: Promise<void> | null = null

  function referencedTagKeys(): Set<string> {
    const keys = new Set<string>()
    for (const f of smartFolders.value) {
      for (const c of f.rules.conditions) {
        if (c.field !== 'tags') continue
        if (Array.isArray(c.value)) {
          for (const v of c.value) keys.add(v)
        } else if (typeof c.value === 'string' && c.value) {
          keys.add(c.value)
        }
      }
    }
    return keys
  }

  async function ensureTagPathsFor(keys: Iterable<string>): Promise<void> {
    const needed: string[] = []
    for (const k of keys) {
      if (k && !cachedTagKeys.has(k)) needed.push(k)
    }
    if (needed.length === 0) return
    if (inflightTagFetch) {
      await inflightTagFetch
      const stillMissing = needed.filter((k) => !cachedTagKeys.has(k))
      if (stillMissing.length === 0) return
      needed.length = 0
      needed.push(...stillMissing)
    }
    const task = (async () => {
      try {
        const raw = await fetchTagPaths(needed)
        for (const k of needed) {
          tagPathSets[k] = new Set(raw[k] ?? [])
          cachedTagKeys.add(k)
        }
        tagPathsVersion.value++
      } catch {
        // Leave cache as-is on failure.
      }
    })()
    inflightTagFetch = task
    try {
      await task
    } finally {
      if (inflightTagFetch === task) inflightTagFetch = null
    }
  }

  async function loadTagPaths(options: { force?: boolean } = {}) {
    if (options.force) {
      cachedTagKeys = new Set()
      tagPathSets = {}
      tagPathsVersion.value++
    }
    await ensureTagPathsFor(referencedTagKeys())
  }

  watch(
    smartFolders,
    () => {
      void ensureTagPathsFor(referencedTagKeys())
    },
    { deep: true },
  )

  // --- API-backed persistence ----------------------------------------

  /** Replace the in-memory folder set with the server's truth. */
  function applyServerFolders(records: FolderRecord[]): void {
    const next_manual: ManualFolder[] = []
    const next_smart: SmartFolder[] = []
    for (const r of records) {
      if (r.kind === 'manual') next_manual.push(recordToManual(r))
      else next_smart.push(recordToSmart(r))
    }
    manualFolders.value = next_manual
    smartFolders.value = next_smart
  }

  /** One-shot import: if the server has nothing and localStorage has the
   * legacy payload, push it up and clear the local copy. Returns true if
   * anything was uploaded (caller should refetch). Guarded by a
   * localStorage flag so it's at-most-once per browser. */
  async function maybeImportLegacy(serverEmpty: boolean): Promise<boolean> {
    if (!serverEmpty) {
      // Server already has data — nothing to import. Clear the legacy
      // blob so subsequent launches don't keep checking.
      localStorage.removeItem(LEGACY_STORAGE_KEY)
      localStorage.setItem(MIGRATION_FLAG_KEY, '1')
      return false
    }
    if (localStorage.getItem(MIGRATION_FLAG_KEY)) return false
    const legacy = readLegacyLocalStorage()
    if (legacy.manual.length === 0 && legacy.smart.length === 0) {
      localStorage.setItem(MIGRATION_FLAG_KEY, '1')
      return false
    }
    try {
      for (const f of legacy.manual) {
        await foldersApi.createFolder({
          id: f.id,
          kind: 'manual',
          name: f.name,
          icon: f.icon,
          items: f.items,
        })
      }
      for (const f of legacy.smart) {
        await foldersApi.createFolder({
          id: f.id,
          kind: 'smart',
          name: f.name,
          icon: f.icon,
          rules: f.rules,
        })
      }
      localStorage.removeItem(LEGACY_STORAGE_KEY)
      localStorage.setItem(MIGRATION_FLAG_KEY, '1')
      return true
    } catch {
      // Leave LEGACY_STORAGE_KEY in place; next launch will retry. Don't
      // set MIGRATION_FLAG_KEY — we don't want a partial import locked in.
      return false
    }
  }

  async function loadFolders(): Promise<void> {
    loading.value = true
    try {
      const first = await foldersApi.fetchFolders()
      const imported = await maybeImportLegacy(first.length === 0)
      const records = imported ? await foldersApi.fetchFolders() : first
      applyServerFolders(records)
    } finally {
      loading.value = false
    }
  }

  const isLibraryScope = computed(() => scope.value.kind === 'library')

  function activeFolder(): AnyFolder | null {
    const s = scope.value
    if (s.kind === 'manual') {
      return manualFolders.value.find((f) => f.id === s.id) ?? null
    }
    if (s.kind === 'smart') {
      return smartFolders.value.find((f) => f.id === s.id) ?? null
    }
    return null
  }

  function setScope(next: FolderScope) {
    scope.value = next
  }

  function scopeMedia(all: Media[]): Media[] {
    void tagPathsVersion.value
    const s = scope.value
    if (s.kind === 'library') return all
    if (s.kind === 'manual') {
      const f = manualFolders.value.find((x) => x.id === s.id)
      if (!f) return []
      const set = new Set(f.items)
      return all.filter((m) => set.has(m.file_path))
    }
    if (s.kind === 'smart') {
      const f = smartFolders.value.find((x) => x.id === s.id)
      if (!f) return []
      return all.filter((m) => matches(m, f.rules))
    }
    return all
  }

  function scopeCount(
    kind: 'library' | 'manual' | 'smart',
    id: string,
    all: Media[],
  ): number {
    void tagPathsVersion.value
    if (kind === 'library') return all.length
    if (kind === 'manual') {
      const f = manualFolders.value.find((x) => x.id === id)
      if (!f) return 0
      const set = new Set(f.items)
      let n = 0
      for (const m of all) if (set.has(m.file_path)) n++
      return n
    }
    if (kind === 'smart') {
      const f = smartFolders.value.find((x) => x.id === id)
      if (!f) return 0
      let n = 0
      for (const m of all) if (matches(m, f.rules)) n++
      return n
    }
    return 0
  }

  function foldersContaining(m: Media, all: Media[]): AnyFolder[] {
    void all
    const out: AnyFolder[] = []
    for (const f of manualFolders.value) {
      if (f.items.includes(m.file_path)) out.push(f)
    }
    for (const f of smartFolders.value) {
      if (matches(m, f.rules)) out.push(f)
    }
    return out
  }

  // --- Mutations (optimistic local + async API) ----------------------

  function createManualFolder(
    name: string,
    initialItems: string[] = [],
  ): ManualFolder {
    const id = randId('f')
    const items = [...new Set(initialItems)]
    const folder: ManualFolder = {
      id,
      name: name.trim() || 'Untitled',
      kind: 'manual',
      icon: 'pi-folder',
      items,
      createdAt: Date.now(),
    }
    manualFolders.value.push(folder)
    // Fire-and-forget: the optimistic folder is the source of truth locally
    // until the server's record comes back or the call fails.
    foldersApi
      .createFolder({
        id,
        kind: 'manual',
        name: folder.name,
        icon: folder.icon,
        items,
      })
      .then((rec) => {
        const i = manualFolders.value.findIndex((f) => f.id === id)
        if (i >= 0) manualFolders.value[i] = recordToManual(rec)
      })
      .catch((e) => {
        const i = manualFolders.value.findIndex((f) => f.id === id)
        if (i >= 0) manualFolders.value.splice(i, 1)
        console.error('Failed to create manual folder', e)
      })
    return folder
  }

  function createSmartFolder(name: string, rules: SmartRules): SmartFolder {
    const id = randId('s')
    const folder: SmartFolder = {
      id,
      name: name.trim() || 'Untitled',
      kind: 'smart',
      icon: 'pi-bolt',
      rules,
      createdAt: Date.now(),
    }
    smartFolders.value.push(folder)
    foldersApi
      .createFolder({
        id,
        kind: 'smart',
        name: folder.name,
        icon: folder.icon,
        rules,
      })
      .then((rec) => {
        const i = smartFolders.value.findIndex((f) => f.id === id)
        if (i >= 0) smartFolders.value[i] = recordToSmart(rec)
      })
      .catch((e) => {
        const i = smartFolders.value.findIndex((f) => f.id === id)
        if (i >= 0) smartFolders.value.splice(i, 1)
        console.error('Failed to create smart folder', e)
      })
    return folder
  }

  function updateSmartFolder(
    id: string,
    patch: Partial<Pick<SmartFolder, 'name' | 'rules' | 'icon'>>,
  ) {
    const i = smartFolders.value.findIndex((f) => f.id === id)
    if (i < 0) return
    const prev = smartFolders.value[i]
    smartFolders.value[i] = { ...prev, ...patch }
    foldersApi.updateFolder(id, patch).catch((e) => {
      const j = smartFolders.value.findIndex((f) => f.id === id)
      if (j >= 0) smartFolders.value[j] = prev
      console.error('Failed to update smart folder', e)
    })
  }

  function renameFolder(id: string, name: string) {
    const trimmed = name.trim()
    if (!trimmed) return
    const m = manualFolders.value.find((f) => f.id === id)
    if (m) {
      const prev = m.name
      m.name = trimmed
      foldersApi.updateFolder(id, { name: trimmed }).catch((e) => {
        m.name = prev
        console.error('Failed to rename folder', e)
      })
      return
    }
    const s = smartFolders.value.find((f) => f.id === id)
    if (s) {
      const prev = s.name
      s.name = trimmed
      foldersApi.updateFolder(id, { name: trimmed }).catch((e) => {
        s.name = prev
        console.error('Failed to rename folder', e)
      })
    }
  }

  function deleteFolder(id: string) {
    const mIdx = manualFolders.value.findIndex((f) => f.id === id)
    const sIdx = mIdx < 0 ? smartFolders.value.findIndex((f) => f.id === id) : -1
    if (mIdx < 0 && sIdx < 0) return
    const prev =
      mIdx >= 0 ? manualFolders.value[mIdx] : smartFolders.value[sIdx]
    if (mIdx >= 0) manualFolders.value.splice(mIdx, 1)
    else smartFolders.value.splice(sIdx, 1)
    if (
      (scope.value.kind === 'manual' || scope.value.kind === 'smart') &&
      scope.value.id === id
    ) {
      scope.value = { kind: 'library' }
    }
    foldersApi.deleteFolder(id).catch((e) => {
      // Put it back on the same list we pulled it from.
      if (mIdx >= 0) {
        manualFolders.value.splice(
          Math.min(mIdx, manualFolders.value.length),
          0,
          prev as ManualFolder,
        )
      } else {
        smartFolders.value.splice(
          Math.min(sIdx, smartFolders.value.length),
          0,
          prev as SmartFolder,
        )
      }
      console.error('Failed to delete folder', e)
    })
  }

  function duplicateSmartFolder(id: string): SmartFolder | null {
    const src = smartFolders.value.find((f) => f.id === id)
    if (!src) return null
    return createSmartFolder(
      src.name + ' (copy)',
      JSON.parse(JSON.stringify(src.rules)) as SmartRules,
    )
  }

  // Adds N paths to a manual folder, returning the number actually added
  // (de-dupes against existing membership).
  function addToManualFolder(folderId: string, paths: string[]): number {
    const f = manualFolders.value.find((x) => x.id === folderId)
    if (!f) return 0
    const existing = new Set(f.items)
    const toAdd: string[] = []
    for (const p of paths) {
      if (!existing.has(p)) {
        toAdd.push(p)
        existing.add(p)
      }
    }
    if (toAdd.length === 0) return 0
    f.items.push(...toAdd)
    foldersApi.addFolderItems(folderId, toAdd).catch((e) => {
      // Roll back the optimistic add.
      const toRemove = new Set(toAdd)
      f.items = f.items.filter((p) => !toRemove.has(p))
      console.error('Failed to add items to folder', e)
    })
    return toAdd.length
  }

  function removeFromManualFolder(folderId: string, paths: string[]): number {
    const f = manualFolders.value.find((x) => x.id === folderId)
    if (!f) return 0
    const toRemove = new Set(paths)
    const removed = f.items.filter((p) => toRemove.has(p))
    if (removed.length === 0) return 0
    f.items = f.items.filter((p) => !toRemove.has(p))
    foldersApi.removeFolderItems(folderId, removed).catch((e) => {
      f.items.push(...removed)
      console.error('Failed to remove items from folder', e)
    })
    return removed.length
  }

  // Keep folders consistent when a file is deleted from the library. The
  // server's ON DELETE CASCADE handles durable state; this is only the
  // local optimistic update so the UI updates without waiting for the WS
  // event to arrive.
  function purgePath(path: string) {
    for (const f of manualFolders.value) {
      const i = f.items.indexOf(path)
      if (i >= 0) f.items.splice(i, 1)
    }
  }

  // --- WS live-sync --------------------------------------------------
  //
  // Handlers are idempotent — a broadcast that echoes our own mutation
  // is absorbed by the "does the id already match?" checks.

  function onFolderCreated(payload: { folder: FolderRecord }): void {
    const r = payload?.folder
    if (!r?.id) return
    const list = r.kind === 'manual' ? manualFolders : smartFolders
    if (list.value.some((f) => f.id === r.id)) return
    if (r.kind === 'manual') manualFolders.value.push(recordToManual(r))
    else smartFolders.value.push(recordToSmart(r))
  }

  function onFolderUpdated(payload: { folder: FolderRecord }): void {
    const r = payload?.folder
    if (!r?.id) return
    if (r.kind === 'manual') {
      const i = manualFolders.value.findIndex((f) => f.id === r.id)
      if (i >= 0) manualFolders.value[i] = recordToManual(r)
    } else {
      const i = smartFolders.value.findIndex((f) => f.id === r.id)
      if (i >= 0) smartFolders.value[i] = recordToSmart(r)
    }
  }

  function onFolderDeleted(payload: { id: string }): void {
    const id = payload?.id
    if (!id) return
    manualFolders.value = manualFolders.value.filter((f) => f.id !== id)
    smartFolders.value = smartFolders.value.filter((f) => f.id !== id)
    if (
      (scope.value.kind === 'manual' || scope.value.kind === 'smart') &&
      scope.value.id === id
    ) {
      scope.value = { kind: 'library' }
    }
  }

  function onFolderItemsChanged(payload: {
    folder_id: string
    added: string[]
    removed: string[]
  }): void {
    const f = manualFolders.value.find((x) => x.id === payload?.folder_id)
    if (!f) return
    const set = new Set(f.items)
    if (Array.isArray(payload.added)) {
      for (const p of payload.added) set.add(p)
    }
    if (Array.isArray(payload.removed)) {
      for (const p of payload.removed) set.delete(p)
    }
    f.items = Array.from(set)
  }

  return {
    manualFolders,
    smartFolders,
    scope,
    loading,
    isLibraryScope,
    tagPathsVersion,
    activeFolder,
    setScope,
    scopeMedia,
    scopeCount,
    foldersContaining,
    loadFolders,
    createManualFolder,
    createSmartFolder,
    updateSmartFolder,
    renameFolder,
    deleteFolder,
    duplicateSmartFolder,
    addToManualFolder,
    removeFromManualFolder,
    purgePath,
    loadTagPaths,
    ensureTagPathsFor,
    onFolderCreated,
    onFolderUpdated,
    onFolderDeleted,
    onFolderItemsChanged,
  }
})

// Reference tables used by the Smart Folder editor — kept module-scoped so
// both the editor and the evaluator agree on what's supported.
export const OP_LABELS: Record<RuleOp, string> = {
  is: 'is',
  is_not: 'is not',
  contains: 'contains',
  does_not_contain: 'does not contain',
  starts_with: 'starts with',
  all_of: 'all of',
  any_of: 'any of',
  within_days: 'within last (days)',
  older_than_days: 'older than (days)',
}

interface FieldDef {
  label: string
  ops: RuleOp[]
  value: 'bool' | 'text' | 'select' | 'tags' | 'days'
  options?: Array<[string, string]>
  defaultValue: () => boolean | string | number | string[]
}

export const FIELD_DEFS: Record<RuleField, FieldDef> = {
  favorite: {
    label: 'Favorite',
    ops: ['is', 'is_not'],
    value: 'bool',
    defaultValue: () => true,
  },
  type: {
    label: 'Type',
    ops: ['is', 'is_not'],
    value: 'select',
    options: [
      ['image', 'Image'],
      ['video', 'Video'],
    ],
    defaultValue: () => 'image',
  },
  model: {
    label: 'Model',
    ops: ['is', 'is_not', 'contains'],
    value: 'text',
    defaultValue: () => '',
  },
  filename: {
    label: 'Filename',
    ops: ['contains', 'does_not_contain', 'starts_with'],
    value: 'text',
    defaultValue: () => '',
  },
  tags: {
    label: 'Tags',
    ops: ['all_of', 'any_of'],
    value: 'tags',
    defaultValue: () => [],
  },
  modified: {
    label: 'Modified',
    ops: ['within_days', 'older_than_days'],
    value: 'days',
    defaultValue: () => 30,
  },
  added: {
    label: 'Added',
    ops: ['within_days', 'older_than_days'],
    value: 'days',
    defaultValue: () => 30,
  },
}
