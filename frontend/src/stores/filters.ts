import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { FilterData, ActiveFilters } from '../types/filters'
import { fetchFilterDataTimed } from '../api/filters'
import { useMediaStore } from './media'
import { now, since } from '../utils/timing'

export type ViewPreset = 'home' | 'video' | 'images' | 'favorites'

export const VIDEO_EXTENSIONS = ['.mp4', '.webm']
export const IMAGE_EXTENSIONS = ['.png', '.jpg', '.jpeg', '.webp', '.gif']

export const useFilterStore = defineStore('filters', () => {
  const filterData = ref<FilterData>({})
  const activeFilters = ref<ActiveFilters>({})
  const activeView = ref<ViewPreset>('home')
  const loading = ref(false)

  async function loadFilterData() {
    loading.value = true
    const t0 = now()
    try {
      const { data, phases } = await fetchFilterDataTimed()
      const t1 = now()
      filterData.value = data
      const groupCount = Object.keys(data).length
      // eslint-disable-next-line no-console
      console.info(
        `[perf] loadFilterData: ttfb=${phases.ttfb.toFixed(0)}ms `
          + `body=${phases.body.toFixed(0)}ms parse=${phases.parse.toFixed(0)}ms `
          + `assign=${since(t1)} total=${since(t0)} `
          + `groups=${groupCount} bytes=${(phases.bytes / 1024).toFixed(0)}KB`,
      )
    } finally {
      loading.value = false
    }
  }

  function setFilter(type: string, keys: string[]) {
    if (keys.length === 0) {
      delete activeFilters.value[type]
    } else {
      activeFilters.value[type] = keys
    }
  }

  function clearFilter(type: string) {
    delete activeFilters.value[type]
  }

  function clearAllFilters() {
    activeFilters.value = {}
    activeView.value = 'home'
    useMediaStore().favoritesOnly = false
  }

  function hasActiveFilters(): boolean {
    return (
      Object.values(activeFilters.value).some((v) => v.length > 0) ||
      activeView.value !== 'home'
    )
  }

  function setView(view: ViewPreset) {
    activeView.value = view
    const media = useMediaStore()
    if (view === 'video') {
      activeFilters.value.ext = [...VIDEO_EXTENSIONS]
      media.favoritesOnly = false
    } else if (view === 'images') {
      activeFilters.value.ext = [...IMAGE_EXTENSIONS]
      media.favoritesOnly = false
    } else if (view === 'favorites') {
      delete activeFilters.value.ext
      media.favoritesOnly = true
    } else {
      delete activeFilters.value.ext
      media.favoritesOnly = false
    }
  }

  return {
    filterData,
    activeFilters,
    activeView,
    loading,
    loadFilterData,
    setFilter,
    clearFilter,
    clearAllFilters,
    hasActiveFilters,
    setView,
  }
})
