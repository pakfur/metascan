<script setup lang="ts">
import { watch } from 'vue'
import { useFilterStore } from '../../stores/filters'
import { useMediaStore } from '../../stores/media'
import FilterSection from './FilterSection.vue'
import ContentSearch from './ContentSearch.vue'

const filterStore = useFilterStore()
const mediaStore = useMediaStore()

// Display order and labels for filter types
const filterSections = [
  { type: 'source', label: 'Source' },
  { type: 'model', label: 'Model' },
  { type: 'ext', label: 'File Type' },
  { type: 'lora', label: 'LoRA' },
  { type: 'tag', label: 'Tag' },
]

// Whenever activeFilters change, apply them
watch(
  () => filterStore.activeFilters,
  (filters) => {
    mediaStore.applyActiveFilters({ ...filters })
  },
  { deep: true }
)

function onClearAll() {
  filterStore.clearAllFilters()
  mediaStore.clearFilters()
}
</script>

<template>
  <div class="filter-panel">
    <div class="filter-header">
      <span class="filter-title">Filters</span>
      <button
        v-if="filterStore.hasActiveFilters()"
        class="clear-all-btn"
        @click="onClearAll"
      >
        Clear All
      </button>
    </div>

    <ContentSearch />

    <div class="filter-sections">
      <FilterSection
        v-for="section in filterSections"
        :key="section.type"
        :label="section.label"
        :type="section.type"
        :items="filterStore.filterData[section.type] || []"
        :selected="filterStore.activeFilters[section.type] || []"
        @update:selected="filterStore.setFilter(section.type, $event)"
        @clear="filterStore.clearFilter(section.type)"
      />
    </div>
  </div>
</template>

<style scoped>
.filter-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  padding: 12px;
  gap: 8px;
}

.filter-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.filter-title {
  font-weight: 700;
  font-size: 15px;
  color: var(--text-color);
}

.clear-all-btn {
  font-size: 12px;
  color: var(--primary-color);
  background: none;
  border: none;
  cursor: pointer;
  padding: 2px 6px;
}

.clear-all-btn:hover {
  text-decoration: underline;
}

.filter-sections {
  display: flex;
  flex-direction: column;
  gap: 4px;
  overflow-y: auto;
  flex: 1;
}
</style>
