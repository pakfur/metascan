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

const STORAGE_KEY = 'metascan.folders.v1'

interface PersistedState {
  manual: ManualFolder[]
  smart: SmartFolder[]
}

function randId(prefix: string): string {
  return prefix + '_' + Math.random().toString(36).slice(2, 8)
}

function loadPersisted(): PersistedState {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return { manual: [], smart: [] }
    const parsed = JSON.parse(raw) as PersistedState
    return {
      manual: Array.isArray(parsed.manual) ? parsed.manual : [],
      smart: Array.isArray(parsed.smart) ? parsed.smart : [],
    }
  } catch {
    return { manual: [], smart: [] }
  }
}

function normalizeModel(m: Media): string {
  // `model` is a string[] on details; take the first entry as the canonical
  // value for rule comparison. Summary records don't ship model; in that
  // case we have no signal — condition just fails.
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
    case 'prompt':
    case 'filename': {
      const hay = (
        field === 'prompt'
          ? m.prompt ?? ''
          : m.file_name ?? fileName(m.file_path)
      ).toLowerCase()
      const needle = String(value ?? '').toLowerCase()
      if (op === 'contains') return hay.includes(needle)
      if (op === 'does_not_contain') return !hay.includes(needle)
      if (op === 'starts_with') return hay.startsWith(needle)
      return false
    }
    case 'tags': {
      const tags = m.tags ?? []
      const vals = Array.isArray(value)
        ? value
        : value === undefined || value === null || value === ''
          ? []
          : [String(value)]
      if (op === 'contains') return vals.every((v) => tags.includes(v))
      if (op === 'contains_any') return vals.some((v) => tags.includes(v))
      if (op === 'does_not_contain') return !vals.some((v) => tags.includes(v))
      return false
    }
    case 'modified': {
      const iso = m.modified_at
      if (!iso) return false
      const t = Date.parse(iso)
      if (Number.isNaN(t)) return false
      const days = Number(value) || 0
      const cutoff = Date.now() - days * 86400000
      if (op === 'within_days') return t >= cutoff
      if (op === 'older_than_days') return t < cutoff
      return false
    }
    case 'dimensions': {
      const isLandscape = m.width > m.height
      const isPortrait = m.width < m.height
      const isSquare = m.width === m.height
      if (op === 'is') {
        if (value === 'landscape') return isLandscape
        if (value === 'portrait') return isPortrait
        if (value === 'square') return isSquare
      }
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
  const initial = loadPersisted()
  const manualFolders = ref<ManualFolder[]>(initial.manual)
  const smartFolders = ref<SmartFolder[]>(initial.smart)
  const scope = ref<FolderScope>({ kind: 'library' })

  watch(
    [manualFolders, smartFolders],
    () => {
      try {
        localStorage.setItem(
          STORAGE_KEY,
          JSON.stringify({
            manual: manualFolders.value,
            smart: smartFolders.value,
          }),
        )
      } catch {
        // Quota / privacy-mode / SSR — folders are ephemeral this session.
      }
    },
    { deep: true },
  )

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

  // Count without materializing the full filtered list — cheaper for the
  // sidebar where every folder row wants its own count.
  function scopeCount(kind: 'library' | 'manual' | 'smart', id: string, all: Media[]): number {
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

  function createManualFolder(name: string, initialItems: string[] = []): ManualFolder {
    const f: ManualFolder = {
      id: randId('f'),
      name: name.trim() || 'Untitled',
      kind: 'manual',
      icon: 'pi-folder',
      items: [...new Set(initialItems)],
      createdAt: Date.now(),
    }
    manualFolders.value.push(f)
    return f
  }

  function createSmartFolder(name: string, rules: SmartRules): SmartFolder {
    const f: SmartFolder = {
      id: randId('s'),
      name: name.trim() || 'Untitled',
      kind: 'smart',
      icon: 'pi-bolt',
      rules,
      createdAt: Date.now(),
    }
    smartFolders.value.push(f)
    return f
  }

  function updateSmartFolder(id: string, patch: Partial<Pick<SmartFolder, 'name' | 'rules' | 'icon'>>) {
    const i = smartFolders.value.findIndex((f) => f.id === id)
    if (i < 0) return
    smartFolders.value[i] = { ...smartFolders.value[i], ...patch }
  }

  function renameFolder(id: string, name: string) {
    const m = manualFolders.value.find((f) => f.id === id)
    if (m) {
      m.name = name.trim() || m.name
      return
    }
    const s = smartFolders.value.find((f) => f.id === id)
    if (s) s.name = name.trim() || s.name
  }

  function deleteFolder(id: string) {
    manualFolders.value = manualFolders.value.filter((f) => f.id !== id)
    smartFolders.value = smartFolders.value.filter((f) => f.id !== id)
    if (
      (scope.value.kind === 'manual' || scope.value.kind === 'smart') &&
      scope.value.id === id
    ) {
      scope.value = { kind: 'library' }
    }
  }

  function duplicateSmartFolder(id: string): SmartFolder | null {
    const src = smartFolders.value.find((f) => f.id === id)
    if (!src) return null
    const copy: SmartFolder = {
      ...src,
      id: randId('s'),
      name: src.name + ' (copy)',
      rules: JSON.parse(JSON.stringify(src.rules)),
      createdAt: Date.now(),
    }
    smartFolders.value.push(copy)
    return copy
  }

  // Adds N paths to a manual folder, returning the number actually added
  // (de-dupes against existing membership).
  function addToManualFolder(folderId: string, paths: string[]): number {
    const f = manualFolders.value.find((x) => x.id === folderId)
    if (!f) return 0
    let added = 0
    const existing = new Set(f.items)
    for (const p of paths) {
      if (!existing.has(p)) {
        f.items.push(p)
        existing.add(p)
        added++
      }
    }
    return added
  }

  function removeFromManualFolder(folderId: string, paths: string[]): number {
    const f = manualFolders.value.find((x) => x.id === folderId)
    if (!f) return 0
    const toRemove = new Set(paths)
    const before = f.items.length
    f.items = f.items.filter((p) => !toRemove.has(p))
    return before - f.items.length
  }

  // Keep folders consistent when a file is deleted from the library.
  function purgePath(path: string) {
    for (const f of manualFolders.value) {
      const i = f.items.indexOf(path)
      if (i >= 0) f.items.splice(i, 1)
    }
  }

  return {
    manualFolders,
    smartFolders,
    scope,
    isLibraryScope,
    activeFolder,
    setScope,
    scopeMedia,
    scopeCount,
    foldersContaining,
    createManualFolder,
    createSmartFolder,
    updateSmartFolder,
    renameFolder,
    deleteFolder,
    duplicateSmartFolder,
    addToManualFolder,
    removeFromManualFolder,
    purgePath,
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
  contains_any: 'contains any of',
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
  prompt: {
    label: 'Prompt',
    ops: ['contains', 'does_not_contain', 'starts_with'],
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
    ops: ['contains', 'contains_any', 'does_not_contain'],
    value: 'tags',
    defaultValue: () => [],
  },
  modified: {
    label: 'Modified',
    ops: ['within_days', 'older_than_days'],
    value: 'days',
    defaultValue: () => 30,
  },
  dimensions: {
    label: 'Orientation',
    ops: ['is'],
    value: 'select',
    options: [
      ['landscape', 'Landscape'],
      ['portrait', 'Portrait'],
      ['square', 'Square'],
    ],
    defaultValue: () => 'landscape',
  },
}
