import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { Media } from '../types/media'
import type { ActiveFilters } from '../types/filters'
import { fetchAllMedia, updateMedia, deleteMedia } from '../api/media'
import { applyFilters } from '../api/filters'

export const useMediaStore = defineStore('media', () => {
  const allMedia = ref<Media[]>([])
  const filteredPaths = ref<Set<string> | null>(null)
  const favoritePaths = ref<Set<string>>(new Set())
  const selectedMedia = ref<Media | null>(null)
  const selectedPaths = ref<Set<string>>(new Set())
  const sortOrder = ref('date_added')
  const favoritesOnly = ref(false)
  const loading = ref(false)

  const displayedMedia = computed(() => {
    let items = allMedia.value

    if (favoritesOnly.value) {
      items = items.filter((m) => m.is_favorite)
    }

    if (filteredPaths.value) {
      const paths = filteredPaths.value
      items = items.filter((m) => paths.has(m.file_path))
    }

    return items
  })

  async function loadAllMedia() {
    loading.value = true
    try {
      allMedia.value = await fetchAllMedia(sortOrder.value)
      favoritePaths.value = new Set(
        allMedia.value.filter((m) => m.is_favorite).map((m) => m.file_path)
      )
    } finally {
      loading.value = false
    }
  }

  async function applyActiveFilters(filters: ActiveFilters) {
    const hasFilters = Object.values(filters).some((v) => v.length > 0)
    if (!hasFilters) {
      filteredPaths.value = null
      return
    }
    const result = await applyFilters(filters)
    filteredPaths.value = new Set(result.paths)
  }

  function clearFilters() {
    filteredPaths.value = null
  }

  function selectMedia(media: Media | null) {
    selectedMedia.value = media
  }

  async function toggleFavorite(media: Media) {
    const updated = await updateMedia(media.file_path, { is_favorite: !media.is_favorite })
    // Update in allMedia
    const idx = allMedia.value.findIndex((m) => m.file_path === media.file_path)
    if (idx >= 0) allMedia.value[idx] = updated
    if (selectedMedia.value?.file_path === media.file_path) {
      selectedMedia.value = updated
    }
  }

  async function removeMedia(media: Media) {
    await deleteMedia(media.file_path)
    allMedia.value = allMedia.value.filter((m) => m.file_path !== media.file_path)
    if (selectedMedia.value?.file_path === media.file_path) {
      selectedMedia.value = null
    }
  }

  function setSortOrder(order: string) {
    sortOrder.value = order
    // Re-sort in-place
    if (order === 'file_name') {
      allMedia.value.sort((a, b) => a.file_name.localeCompare(b.file_name))
    } else if (order === 'date_modified') {
      allMedia.value.sort((a, b) => {
        const da = a.modified_at || a.created_at || ''
        const db = b.modified_at || b.created_at || ''
        return db.localeCompare(da)
      })
    }
    // date_added is the default order from the API
  }

  return {
    allMedia,
    displayedMedia,
    selectedMedia,
    selectedPaths,
    sortOrder,
    favoritesOnly,
    loading,
    loadAllMedia,
    applyActiveFilters,
    clearFilters,
    selectMedia,
    toggleFavorite,
    removeMedia,
    setSortOrder,
  }
})
