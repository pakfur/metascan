<script setup lang="ts">
import { watch } from 'vue'
import { useFilterStore } from '../../stores/filters'
import { useMediaStore } from '../../stores/media'
import FilterSection from './FilterSection.vue'
import FoldersSection from './FoldersSection.vue'

const filterStore = useFilterStore()
const mediaStore = useMediaStore()

const filterSections = [
  { type: 'camera_make', label: 'Camera Make' },
  { type: 'camera_model', label: 'Camera Model' },
  { type: 'has_gps', label: 'Has Location' },
  { type: 'model', label: 'Model' },
  { type: 'lora', label: 'LoRA' },
  { type: 'tag', label: 'Tags' },
]

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
    <div class="app-title-block">
      <span class="app-title">Metascan</span>
    </div>

    <!-- Folders + Smart Folders ride above the standard filter list. -->
    <div class="folders-stack">
      <FoldersSection kind="manual" label="FOLDERS" />
      <FoldersSection kind="smart" label="SMART FOLDERS" />
    </div>

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

.app-title-block {
  padding-bottom: 8px;
  border-bottom: 1px solid var(--surface-border);
  margin-bottom: 4px;
}

.app-title {
  font-weight: 700;
  font-size: 18px;
  color: var(--primary-color);
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

.folders-stack {
  display: flex;
  flex-direction: column;
}

.filter-sections {
  display: flex;
  flex-direction: column;
  gap: 4px;
  overflow-y: auto;
  flex: 1;
}
</style>
