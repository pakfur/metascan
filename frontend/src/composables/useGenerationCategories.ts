import { ref, watch, type Ref } from 'vue'
import { ApiError } from '../api/client'
import { useCategoriesStore } from '../stores/categories'

// The categories a generation dialog tags its output with. Session-local UI
// state, remembered per browser under `storageKey` — never part of a tile's
// form_state, so selecting an older image or clip cannot change it (the T2I
// autosave-drift rule). Ids whose category no longer exists are dropped once
// the list has been loaded.

function readIds(key: string): number[] {
  try {
    const raw = localStorage.getItem(key)
    if (raw === null) return []
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return [...new Set(parsed.filter((v): v is number => Number.isInteger(v)))]
  } catch {
    return []
  }
}

function writeIds(key: string, ids: number[]): void {
  try {
    localStorage.setItem(key, JSON.stringify(ids))
  } catch {
    /* private mode: the choice lasts for this session */
  }
}

/** True for the 404 a generate route answers when a chosen category is gone. */
export function isUnknownCategoryError(e: unknown): boolean {
  return (
    e instanceof ApiError &&
    e.status === 404 &&
    typeof e.detail === 'object' &&
    e.detail !== null &&
    (e.detail as { code?: unknown }).code === 'unknown_category'
  )
}

export function useGenerationCategories(storageKey: string): {
  selected: Ref<number[]>
  refresh: () => Promise<void>
} {
  const store = useCategoriesStore()
  const selected = ref<number[]>(readIds(storageKey))
  // Until the list has loaded, an empty store means "not known yet", not
  // "every category is gone".
  let loaded = false

  function prune(): void {
    if (!loaded) return
    const known = new Set(store.categories.map((c) => c.id))
    const kept = selected.value.filter((id) => known.has(id))
    if (kept.length !== selected.value.length) selected.value = kept
  }

  async function refresh(): Promise<void> {
    try {
      await store.load()
      loaded = true
      prune()
    } catch (e) {
      console.error('Failed to load categories', e)
    }
  }

  watch(() => store.categories, prune)
  watch(selected, (ids) => writeIds(storageKey, ids))
  void refresh()

  return { selected, refresh }
}
