<script setup lang="ts">
import { ref, onMounted } from 'vue'
import type { Media } from './types/media'
import { useMediaStore } from './stores/media'
import { useFilterStore } from './stores/filters'
import { useSettingsStore } from './stores/settings'
import { useKeyboard } from './composables/useKeyboard'
import AppHeader from './components/layout/AppHeader.vue'
import ThreePanel from './components/layout/ThreePanel.vue'
import FilterPanel from './components/filters/FilterPanel.vue'
import ThumbnailGrid from './components/thumbnails/ThumbnailGrid.vue'
import MetadataPanel from './components/metadata/MetadataPanel.vue'
import MediaViewer from './components/viewer/MediaViewer.vue'
import SlideshowViewer from './components/viewer/SlideshowViewer.vue'

const mediaStore = useMediaStore()
const filterStore = useFilterStore()
const settingsStore = useSettingsStore()

// Viewer state
const viewerOpen = ref(false)
const viewerIndex = ref(0)
const slideshowOpen = ref(false)

onMounted(async () => {
  await Promise.all([
    settingsStore.loadConfig(),
    mediaStore.loadAllMedia(),
    filterStore.loadFilterData(),
  ])
})

function openViewer(media: Media) {
  const idx = mediaStore.displayedMedia.findIndex(
    (m) => m.file_path === media.file_path
  )
  viewerIndex.value = idx >= 0 ? idx : 0
  viewerOpen.value = true
}

function closeViewer() {
  viewerOpen.value = false
}

function openSlideshow() {
  if (mediaStore.displayedMedia.length > 0) {
    slideshowOpen.value = true
  }
}

function closeSlideshow() {
  slideshowOpen.value = false
}

useKeyboard([
  { key: 'F5', handler: () => mediaStore.loadAllMedia() },
  {
    key: 'Escape',
    handler: () => {
      if (!viewerOpen.value && !slideshowOpen.value) {
        mediaStore.selectMedia(null)
      }
    },
  },
  { key: 's', ctrl: true, shift: true, handler: openSlideshow },
])
</script>

<template>
  <div class="app-shell">
    <AppHeader @slideshow="openSlideshow" />

    <ThreePanel>
      <template #left>
        <FilterPanel />
      </template>

      <template #center>
        <ThumbnailGrid @open="openViewer" />
      </template>

      <template #right>
        <MetadataPanel />
      </template>
    </ThreePanel>

    <!-- Media Viewer overlay -->
    <MediaViewer
      v-if="viewerOpen"
      :media-list="mediaStore.displayedMedia"
      :initial-index="viewerIndex"
      @close="closeViewer"
    />

    <!-- Slideshow overlay -->
    <SlideshowViewer
      v-if="slideshowOpen"
      :media-list="mediaStore.displayedMedia"
      @close="closeSlideshow"
    />
  </div>
</template>

<style scoped>
.app-shell {
  display: flex;
  flex-direction: column;
  height: 100%;
}
</style>
