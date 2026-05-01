import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { Media } from '../types/media'
import { searchSimilar, contentSearch } from '../api/similarity'
import { ApiError } from '../api/client'

export interface DimMismatch {
  message: string
  index_dim: number
  model_dim: number
  index_model_key: string | null
}

export const useSimilarityStore = defineStore('similarity', () => {
  const active = ref(false)
  const referenceMedia = ref<Media | null>(null)
  const results = ref<Media[]>([])
  // Image↔image CLIP similarity is usually 0.4-0.9, so 0.7 is a sensible
  // "strongly similar" cut for the Find Similar flow.
  const threshold = ref(0.7)
  // Text↔image CLIP similarity lives on a much lower scale (~0.15-0.35),
  // so the content-search slider is calibrated to 0-0.45 with its own
  // default of 0 (i.e. show every result the server returned).
  const contentThreshold = ref(0)
  const loading = ref(false)
  const contentQuery = ref('')
  const isContentSearch = ref(false)
  const dimMismatch = ref<DimMismatch | null>(null)
  const searchError = ref<string | null>(null)

  const CONTENT_THRESHOLD_MAX = 0.45

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

  const filteredResults = computed(() => {
    const cut = isContentSearch.value ? contentThreshold.value : threshold.value
    return results.value.filter((m) => (m.similarity_score ?? 0) >= cut)
  })

  async function findSimilar(media: Media) {
    active.value = true
    isContentSearch.value = false
    referenceMedia.value = media
    loading.value = true
    try {
      const raw = await searchSimilar(media.file_path, threshold.value)
      results.value = raw as unknown as Media[]
    } catch (e) {
      console.error('Similarity search failed:', e)
      results.value = []
    } finally {
      loading.value = false
    }
  }

  async function searchByText(query: string) {
    active.value = true
    isContentSearch.value = true
    contentQuery.value = query
    referenceMedia.value = null
    loading.value = true
    searchError.value = null
    dimMismatch.value = null
    try {
      const raw = await contentSearch(query)
      results.value = raw as unknown as Media[]
    } catch (e) {
      const mismatch = extractDimMismatch(e)
      if (mismatch) {
        dimMismatch.value = mismatch
      } else {
        searchError.value = e instanceof Error ? e.message : String(e)
      }
      results.value = []
      console.error('Content search failed:', e)
    } finally {
      loading.value = false
    }
  }

  function clearSearchError() {
    searchError.value = null
    dimMismatch.value = null
  }

  async function updateThreshold(newThreshold: number) {
    if (isContentSearch.value) {
      // Content search is pure client-side filtering over the server's
      // top-K; no re-request needed.
      contentThreshold.value = Math.min(newThreshold, CONTENT_THRESHOLD_MAX)
      return
    }
    threshold.value = newThreshold
    // Re-fetch with new threshold for image↔image similarity search.
    if (active.value && referenceMedia.value) {
      loading.value = true
      try {
        const raw = await searchSimilar(referenceMedia.value.file_path, newThreshold)
        results.value = raw as unknown as Media[]
      } finally {
        loading.value = false
      }
    }
  }

  function exit() {
    active.value = false
    referenceMedia.value = null
    results.value = []
    contentQuery.value = ''
    isContentSearch.value = false
    searchError.value = null
    dimMismatch.value = null
  }

  return {
    active,
    referenceMedia,
    results,
    threshold,
    contentThreshold,
    CONTENT_THRESHOLD_MAX,
    loading,
    contentQuery,
    isContentSearch,
    dimMismatch,
    searchError,
    filteredResults,
    findSimilar,
    searchByText,
    updateThreshold,
    clearSearchError,
    exit,
  }
})
