import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { FilterData, ActiveFilters } from '../types/filters'
import { fetchFilterData } from '../api/filters'

export const useFilterStore = defineStore('filters', () => {
  const filterData = ref<FilterData>({})
  const activeFilters = ref<ActiveFilters>({})
  const contentSearchQuery = ref('')
  const loading = ref(false)

  async function loadFilterData() {
    loading.value = true
    try {
      filterData.value = await fetchFilterData()
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
    contentSearchQuery.value = ''
  }

  function hasActiveFilters(): boolean {
    return Object.values(activeFilters.value).some((v) => v.length > 0) || contentSearchQuery.value !== ''
  }

  return {
    filterData,
    activeFilters,
    contentSearchQuery,
    loading,
    loadFilterData,
    setFilter,
    clearFilter,
    clearAllFilters,
    hasActiveFilters,
  }
})
