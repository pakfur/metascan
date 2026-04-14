<script setup lang="ts">
import { onMounted } from 'vue'
import { useMediaStore } from './stores/media'
import { useFilterStore } from './stores/filters'
import { useSettingsStore } from './stores/settings'
import { useKeyboard } from './composables/useKeyboard'
import AppHeader from './components/layout/AppHeader.vue'
import ThreePanel from './components/layout/ThreePanel.vue'
import FilterPanel from './components/filters/FilterPanel.vue'
import ThumbnailGrid from './components/thumbnails/ThumbnailGrid.vue'
import MetadataPanel from './components/metadata/MetadataPanel.vue'

const mediaStore = useMediaStore()
const filterStore = useFilterStore()
const settingsStore = useSettingsStore()

onMounted(async () => {
  await Promise.all([
    settingsStore.loadConfig(),
    mediaStore.loadAllMedia(),
    filterStore.loadFilterData(),
  ])
})

useKeyboard([
  { key: 'F5', handler: () => mediaStore.loadAllMedia() },
  { key: 'Escape', handler: () => mediaStore.selectMedia(null) },
])
</script>

<template>
  <div class="app-shell">
    <AppHeader />

    <ThreePanel>
      <template #left>
        <FilterPanel />
      </template>

      <template #center>
        <ThumbnailGrid />
      </template>

      <template #right>
        <MetadataPanel />
      </template>
    </ThreePanel>
  </div>
</template>

<style scoped>
.app-shell {
  display: flex;
  flex-direction: column;
  height: 100%;
}
</style>
