import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { Media } from '../types/media'
import type { ActiveFilters } from '../types/filters'
import { fetchAllMedia, fetchMediaDetails, updateMedia, deleteMedia } from '../api/media'
import { applyFilters } from '../api/filters'
import { useFoldersStore } from './folders'
import { useSearchStore } from './search'

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
  const showHidden = ref(false)
  const loading = ref(false)
  const detailLoading = ref(false)

  const displayedMedia = computed(() => {
    let items = allMedia.value

    // allMedia is always fetched with include_hidden=true; visibility is a
    // client-side rule so folder scope can make exceptions. Hidden media
    // stay out of the grid, except storyboard video clips (hidden at
    // ingest, panel_videos rows) which surface inside the manual folder
    // that contains them — i.e. the storyboard's own folder view.
    if (!showHidden.value) {
      const folderSet = useFoldersStore().activeManualItemSet
      items = items.filter(
        (m) =>
          !m.hidden ||
          (m.is_video && folderSet !== null && folderSet.has(m.file_path)),
      )
    }

    if (favoritesOnly.value) {
      items = items.filter((m) => m.is_favorite)
    }

    if (filteredPaths.value) {
      const paths = filteredPaths.value
      items = items.filter((m) => paths.has(m.file_path))
    }

    // Content search + tag-AND search are two more path-set layers, composed
    // like filteredPaths. Stale index paths (deleted files) drop out here
    // naturally because they no longer appear in allMedia.
    const search = useSearchStore()
    if (search.scores) {
      const s = search.scores
      items = items.filter((m) => s.has(m.file_path))
    }
    if (search.tagPaths) {
      const t = search.tagPaths
      items = items.filter((m) => t.has(m.file_path))
    }

    if (sortOrder.value === 'relevance' && search.scores) {
      const s = search.scores
      items = [...items].sort(
        (a, b) => (s.get(b.file_path) ?? 0) - (s.get(a.file_path) ?? 0),
      )
    }

    return items
  })

  // scopedMedia narrows displayedMedia to the active folder/smart-folder
  // scope. Grid, breadcrumb, and viewer all read this — any new surface
  // that should respect the folder scope should use scopedMedia, not
  // displayedMedia.
  const scopedMedia = computed(() => {
    const folders = useFoldersStore()
    return folders.scopeMedia(displayedMedia.value)
  })

  async function loadAllMedia() {
    loading.value = true
    try {
      const data = await fetchAllMedia(sortOrder.value, false, true)
      allMedia.value = data
      favoritePaths.value = new Set(
        data.filter((m) => m.is_favorite).map((m) => m.file_path),
      )
    } finally {
      loading.value = false
    }
  }

  // Hidden media (unpicked storyboard variants + rendered clips) are always
  // fetched; visibility is the client-side rule in displayedMedia above, so
  // toggling needs no refetch.
  function toggleShowHidden() {
    showHidden.value = !showHidden.value
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
    // Keep manual folders consistent — a path that's been deleted from
    // disk shouldn't linger as a dangling member.
    useFoldersStore().purgePath(media.file_path)
  }

  function setSortOrder(order: string) {
    if (order === sortOrder.value) return
    sortOrder.value = order
    // Relevance sorts client-side from the search score map; every other
    // order defers to the server and refetches.
    if (order === 'relevance') return
    loadAllMedia()
  }

  return {
    allMedia,
    displayedMedia,
    scopedMedia,
    selectedMedia,
    selectedPaths,
    sortOrder,
    favoritesOnly,
    showHidden,
    loading,
    detailLoading,
    loadAllMedia,
    applyActiveFilters,
    clearFilters,
    selectMedia,
    toggleFavorite,
    removeMedia,
    setSortOrder,
    toggleShowHidden,
  }
})
