import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { Media } from '../types/media'
import { searchSimilar, contentSearch } from '../api/similarity'

export const useSimilarityStore = defineStore('similarity', () => {
  const active = ref(false)
  const referenceMedia = ref<Media | null>(null)
  const results = ref<Media[]>([])
  const threshold = ref(0.7)
  const loading = ref(false)
  const contentQuery = ref('')
  const isContentSearch = ref(false)

  const filteredResults = computed(() =>
    results.value.filter(
      (m) => (m.similarity_score ?? 0) >= threshold.value
    )
  )

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
    try {
      const raw = await contentSearch(query)
      results.value = raw as unknown as Media[]
    } catch (e) {
      console.error('Content search failed:', e)
      results.value = []
    } finally {
      loading.value = false
    }
  }

  async function updateThreshold(newThreshold: number) {
    threshold.value = newThreshold
    // Re-fetch with new threshold if doing similarity search
    if (active.value && referenceMedia.value && !isContentSearch.value) {
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
  }

  return {
    active,
    referenceMedia,
    results,
    threshold,
    loading,
    contentQuery,
    isContentSearch,
    filteredResults,
    findSimilar,
    searchByText,
    updateThreshold,
    exit,
  }
})
