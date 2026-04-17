import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { Media } from '../types/media'
import type { ActiveFilters } from '../types/filters'
import { fetchAllMedia, fetchMediaDetails, updateMedia, deleteMedia } from '../api/media'
import { applyFilters } from '../api/filters'

export const useMediaStore = defineStore('media', () => {
  // Summary records only. Heavy AI-generation fields are absent from these
  // objects by design — MetadataPanel reads them off `selectedMedia`, which
  // is fetched per-selection below.
  const allMedia = ref<Media[]>([])
  const filteredPaths = ref<Set<string> | null>(null)
  const favoritePaths = ref<Set<string>>(new Set())
  const selectedMedia = ref<Media | null>(null)
  const selectedPaths = ref<Set<string>>(new Set())
  const sortOrder = ref('date_added')
  const favoritesOnly = ref(false)
  const loading = ref(false)
  const detailLoading = ref(false)

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
      const data = await fetchAllMedia(sortOrder.value)
      allMedia.value = data
      favoritePaths.value = new Set(
        data.filter((m) => m.is_favorite).map((m) => m.file_path),
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

  // Fetch-on-select. Heavy fields (prompt, model, loras, tags, ...) are not
  // in the summary list, so every selection hits GET /api/media/{path}. We
  // deliberately don't cache the previous detail object — a fresh fetch on
  // each click keeps the panel in sync with any CLIP tag writes or other
  // background updates.
  //
  // Returns the detail object so callers awaiting selection can use it.
  let selectionToken = 0
  async function selectMedia(summary: Media | null): Promise<Media | null> {
    if (summary === null) {
      selectedMedia.value = null
      return null
    }
    // Guard against races: if the user clicks B while A is still fetching,
    // A's response must not overwrite B's selection.
    const token = ++selectionToken
    // Show the summary immediately so the panel has width/name/etc., then
    // upgrade to the full record when it arrives.
    selectedMedia.value = summary
    detailLoading.value = true
    try {
      const detail = await fetchMediaDetails(summary.file_path)
      if (token === selectionToken) {
        selectedMedia.value = detail
      }
      return detail
    } catch (e) {
      console.error('Failed to fetch media details', e)
      return null
    } finally {
      if (token === selectionToken) {
        detailLoading.value = false
      }
    }
  }

  async function toggleFavorite(media: Media) {
    const updated = await updateMedia(media.file_path, { is_favorite: !media.is_favorite })
    const idx = allMedia.value.findIndex((m) => m.file_path === media.file_path)
    if (idx >= 0) allMedia.value[idx] = updated
    // `updated` is a summary — it lacks prompt/tags/etc. Only mirror the
    // flipped flag onto the current detail record so we don't blow away the
    // AI fields we just loaded for the panel.
    if (selectedMedia.value?.file_path === media.file_path) {
      selectedMedia.value.is_favorite = updated.is_favorite
      selectedMedia.value.playback_speed = updated.playback_speed
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
    if (order === sortOrder.value) return
    sortOrder.value = order
    // Sort fields (`file_name`, `modified_at`) aren't in the summary
    // payload any more, so defer sorting to the server and refetch.
    loadAllMedia()
  }

  return {
    allMedia,
    displayedMedia,
    selectedMedia,
    selectedPaths,
    sortOrder,
    favoritesOnly,
    loading,
    detailLoading,
    loadAllMedia,
    applyActiveFilters,
    clearFilters,
    selectMedia,
    toggleFavorite,
    removeMedia,
    setSortOrder,
  }
})
