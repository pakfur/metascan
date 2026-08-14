import { defineStore } from 'pinia'
import { ref, computed, watch } from 'vue'
import type { Media } from '../types/media'
import { searchSimilar, contentSearch } from '../api/similarity'
import { fetchTagPaths } from '../api/filters'
import { ApiError } from '../api/client'
import { useMediaStore } from './media'
import { useModelsStore } from './models'

export interface DimMismatch {
  message: string
  index_dim: number
  model_dim: number
  index_model_key: string | null
}

// Text↔image CLIP cosine scores live on a much lower scale (~0.15–0.35)
// than image↔image (~0.4–0.9), so the two modes keep separate thresholds
// and slider calibrations.
export const TEXT_THRESHOLD_MAX = 0.45
export const TEXT_THRESHOLD_DEFAULT = 0.2
export const IMAGE_THRESHOLD_DEFAULT = 0.7

type PendingSearch =
  | { kind: 'text'; query: string }
  | { kind: 'similar'; media: Media }

export const useSearchStore = defineStore('search', () => {
  const textQuery = ref('')
  const similarTo = ref<Media | null>(null)
  const tagChips = ref<string[]>([])
  const textThreshold = ref(TEXT_THRESHOLD_DEFAULT)
  const imageThreshold = ref(IMAGE_THRESHOLD_DEFAULT)
  const scores = ref<Map<string, number> | null>(null)
  const tagPaths = ref<Set<string> | null>(null)
  const loading = ref(false)
  const pending = ref<PendingSearch | null>(null)
  const dimMismatch = ref<DimMismatch | null>(null)
  const searchError = ref<string | null>(null)

  const similarityActive = computed(() => scores.value !== null)
  const active = computed(() => scores.value !== null || tagPaths.value !== null)
  const isTextSearch = computed(() => similarTo.value === null)
  const threshold = computed(() =>
    isTextSearch.value ? textThreshold.value : imageThreshold.value,
  )

  const modelsStore = useModelsStore()

  // Coalesce: a search submitted before the inference worker is ready is
  // remembered (most-recent wins) and fired once the worker reaches ready.
  watch(
    () => modelsStore.inferenceState,
    (state) => {
      if (state === 'ready' && pending.value) {
        const p = pending.value
        pending.value = null
        if (p.kind === 'text') void runText(p.query)
        else void runSimilar(p.media)
      }
    },
  )

  function extractDimMismatch(e: unknown): DimMismatch | null {
    if (!(e instanceof ApiError) || e.status !== 409) return null
    const d = e.detail as Record<string, unknown> | null
    if (!d || d.code !== 'dim_mismatch') return null
    return {
      message: typeof d.message === 'string' ? d.message : 'index/model dim mismatch',
      index_dim: Number(d.index_dim) || 0,
      model_dim: Number(d.model_dim) || 0,
      index_model_key:
        typeof d.index_model_key === 'string' ? d.index_model_key : null,
    }
  }

  // Sort handshake: a successful similarity search flips the grid to
  // Relevance, remembering the prior sort; clearing the search restores it.
  let savedSort: string | null = null
  function enterRelevanceSort() {
    const media = useMediaStore()
    if (media.sortOrder !== 'relevance') {
      savedSort = media.sortOrder
      media.setSortOrder('relevance')
    }
  }
  function restoreSort() {
    const media = useMediaStore()
    if (media.sortOrder === 'relevance') {
      media.setSortOrder(savedSort ?? 'date_added')
    }
    savedSort = null
  }

  async function runText(query: string) {
    textQuery.value = query
    similarTo.value = null
    loading.value = true
    searchError.value = null
    dimMismatch.value = null
    try {
      const hits = await contentSearch(query, textThreshold.value)
      scores.value = new Map(hits.map((h) => [h.file_path, h.similarity_score]))
      enterRelevanceSort()
    } catch (e) {
      const mismatch = extractDimMismatch(e)
      if (mismatch) dimMismatch.value = mismatch
      else searchError.value = e instanceof Error ? e.message : String(e)
      restoreSort()
      scores.value = null
      console.error('Content search failed:', e)
    } finally {
      loading.value = false
    }
  }

  async function runSimilar(media: Media) {
    similarTo.value = media
    textQuery.value = ''
    loading.value = true
    searchError.value = null
    dimMismatch.value = null
    try {
      const hits = await searchSimilar(media.file_path, imageThreshold.value)
      scores.value = new Map(hits.map((h) => [h.file_path, h.similarity_score]))
      enterRelevanceSort()
    } catch (e) {
      const mismatch = extractDimMismatch(e)
      if (mismatch) dimMismatch.value = mismatch
      else searchError.value = e instanceof Error ? e.message : String(e)
      restoreSort()
      scores.value = null
      console.error('Similarity search failed:', e)
    } finally {
      loading.value = false
    }
  }

  function submitViaWorker(p: PendingSearch) {
    if (modelsStore.isInferenceReady) {
      pending.value = null
      if (p.kind === 'text') void runText(p.query)
      else void runSimilar(p.media)
      return
    }
    pending.value = p
    const s = modelsStore.inferenceState
    if (s === 'idle' || s === 'stopped' || s === 'error') {
      void modelsStore.startInferenceWorker()
    }
  }

  function submitText(query: string) {
    const q = query.trim()
    if (!q) return
    submitViaWorker({ kind: 'text', query: q })
  }

  function submitSimilar(media: Media) {
    submitViaWorker({ kind: 'similar', media })
  }

  async function searchTags(keys: string[]) {
    tagChips.value = keys
    if (keys.length === 0) {
      tagPaths.value = null
      return
    }
    loading.value = true
    try {
      const byKey = await fetchTagPaths(keys)
      let acc: Set<string> | null = null
      for (const key of keys) {
        const s = new Set(byKey[key] ?? [])
        acc = acc === null ? s : new Set([...acc].filter((p: string) => s.has(p)))
      }
      tagPaths.value = acc ?? new Set()
    } catch (e) {
      searchError.value = e instanceof Error ? e.message : String(e)
      console.error('Tag search failed:', e)
    } finally {
      loading.value = false
    }
  }

  async function setThreshold(value: number) {
    if (isTextSearch.value) {
      textThreshold.value = Math.min(value, TEXT_THRESHOLD_MAX)
    } else {
      imageThreshold.value = value
    }
    // Threshold is applied server-side — re-run the active search.
    if (scores.value !== null) {
      if (similarTo.value) await runSimilar(similarTo.value)
      else if (textQuery.value) await runText(textQuery.value)
    }
  }

  function clearSimilarity() {
    restoreSort()
    scores.value = null
    textQuery.value = ''
    similarTo.value = null
    pending.value = null
    searchError.value = null
    dimMismatch.value = null
  }

  function clearTags() {
    tagChips.value = []
    tagPaths.value = null
  }

  function clearSearchError() {
    searchError.value = null
    dimMismatch.value = null
  }

  function clearAll() {
    clearSimilarity()
    clearTags()
    clearSearchError()
  }

  return {
    textQuery,
    similarTo,
    tagChips,
    textThreshold,
    imageThreshold,
    scores,
    tagPaths,
    loading,
    pending,
    dimMismatch,
    searchError,
    similarityActive,
    active,
    isTextSearch,
    threshold,
    submitText,
    submitSimilar,
    searchTags,
    setThreshold,
    clearSimilarity,
    clearTags,
    clearSearchError,
    clearAll,
  }
})
