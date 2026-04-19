<script setup lang="ts">
import { ref, onMounted, nextTick } from 'vue'
import type { Media } from './types/media'
import { useMediaStore } from './stores/media'
import { useFilterStore } from './stores/filters'
import { useSettingsStore } from './stores/settings'
import { useScanStore } from './stores/scan'
import { useSimilarityStore } from './stores/similarity'
import { useKeyboard } from './composables/useKeyboard'
import { useWebSocket } from './composables/useWebSocket'
import ThreePanel from './components/layout/ThreePanel.vue'
import ContentSearchBar from './components/layout/ContentSearchBar.vue'
import ViewMenubar from './components/layout/ViewMenubar.vue'
import FilterPanel from './components/filters/FilterPanel.vue'
import ThumbnailGrid from './components/thumbnails/ThumbnailGrid.vue'
import MetadataPanel from './components/metadata/MetadataPanel.vue'
import MediaViewer from './components/viewer/MediaViewer.vue'
import SlideshowViewer from './components/viewer/SlideshowViewer.vue'
import ScanDialog from './components/dialogs/ScanDialog.vue'
import SimilaritySettings from './components/dialogs/SimilaritySettings.vue'
import DuplicateFinder from './components/dialogs/DuplicateFinder.vue'
import UpscaleDialog from './components/dialogs/UpscaleDialog.vue'
import UpscaleQueue from './components/dialogs/UpscaleQueue.vue'
import ConfigDialog from './components/dialogs/ConfigDialog.vue'
import ScopeBreadcrumb from './components/layout/ScopeBreadcrumb.vue'
import NewFolderDialog from './components/dialogs/NewFolderDialog.vue'
import SmartFolderEditor from './components/dialogs/SmartFolderEditor.vue'
import FolderKebabMenu from './components/filters/FolderKebabMenu.vue'
import ToastHost from './components/layout/ToastHost.vue'
import { useFoldersUi } from './composables/useFoldersUi'

const mediaStore = useMediaStore()
const filterStore = useFilterStore()
const settingsStore = useSettingsStore()
const scanStore = useScanStore()
const simStore = useSimilarityStore()
const foldersUi = useFoldersUi()

const thumbnailGridRef = ref<InstanceType<typeof ThumbnailGrid> | null>(null)

// Dialog state
const viewerOpen = ref(false)
const viewerIndex = ref(0)
const slideshowOpen = ref(false)
const simSettingsOpen = ref(false)
const dupFinderOpen = ref(false)
const upscaleDialogOpen = ref(false)
const upscaleQueueOpen = ref(false)
const upscaleTargets = ref<Media[]>([])
const configOpen = ref(false)

onMounted(async () => {
  await Promise.all([
    settingsStore.loadConfig(),
    mediaStore.loadAllMedia(),
    filterStore.loadFilterData(),
  ])
})

// File watcher: auto-refresh when files change on disk
let watcherDebounce: ReturnType<typeof setTimeout> | null = null
useWebSocket('watcher', () => {
  // Debounce rapid file changes into a single refresh
  if (watcherDebounce) clearTimeout(watcherDebounce)
  watcherDebounce = setTimeout(() => {
    mediaStore.loadAllMedia()
    filterStore.loadFilterData()
  }, 2000)
})

function openViewer(media: Media) {
  // Viewer navigates within the active scope (library / manual / smart) so
  // prev/next stays inside the folder the user just clicked into.
  const idx = mediaStore.scopedMedia.findIndex(
    (m) => m.file_path === media.file_path,
  )
  viewerIndex.value = idx >= 0 ? idx : 0
  viewerOpen.value = true
}

function closeViewer() {
  viewerOpen.value = false
  // Wait for ThumbnailGrid to repaint (the overlay's v-if tears down first),
  // then bring the last-viewed item into view.
  nextTick(() => {
    thumbnailGridRef.value?.scrollSelectedIntoView()
  })
}

function openSlideshow() {
  if (mediaStore.scopedMedia.length > 0) {
    slideshowOpen.value = true
  }
}

function closeSlideshow() {
  slideshowOpen.value = false
}

function openScan() {
  scanStore.prepare()
}

function closeScan() {
  if (scanStore.phase === 'complete' || scanStore.embeddingPhase === 'complete') {
    mediaStore.loadAllMedia()
    filterStore.loadFilterData()
  }
  scanStore.reset()
  scanStore.resetEmbedding()
}

function openUpscale(items: Media[]) {
  upscaleTargets.value = items
  upscaleDialogOpen.value = true
}

function handleUpscaleFromSelected() {
  if (mediaStore.selectedMedia) {
    openUpscale([mediaStore.selectedMedia])
  }
}

useKeyboard([
  { key: 'F5', handler: () => mediaStore.loadAllMedia() },
  {
    key: 'Escape',
    handler: () => {
      if (simStore.active) {
        simStore.exit()
      } else if (!viewerOpen.value && !slideshowOpen.value) {
        mediaStore.selectMedia(null)
      }
    },
  },
  { key: 's', ctrl: true, shift: true, handler: openSlideshow },
  { key: 's', ctrl: true, handler: openScan },
  { key: 'd', ctrl: true, shift: true, handler: () => { dupFinderOpen.value = true } },
  { key: 'u', ctrl: true, handler: handleUpscaleFromSelected },
])
</script>

<template>
  <div class="app-shell">
    <ThreePanel>
      <template #left>
        <FilterPanel />
      </template>

      <template #center>
        <div class="center-stack">
          <ContentSearchBar
            @scan="openScan"
            @refresh="mediaStore.loadAllMedia()"
            @upscale-queue="upscaleQueueOpen = true"
            @find-duplicates="dupFinderOpen = true"
            @similarity-settings="simSettingsOpen = true"
            @config="configOpen = true"
          />
          <ViewMenubar @slideshow="openSlideshow" />
          <ScopeBreadcrumb />
          <div class="grid-wrap">
            <ThumbnailGrid
              ref="thumbnailGridRef"
              @open="openViewer"
              @upscale="openUpscale"
            />
          </div>
        </div>
      </template>

      <template #right>
        <MetadataPanel />
      </template>
    </ThreePanel>

    <!-- Loading overlay -->
    <div v-if="mediaStore.loading" class="loading-overlay">
      <div class="loading-spinner" />
      <span>Loading media...</span>
    </div>

    <!-- Media Viewer overlay -->
    <MediaViewer
      v-if="viewerOpen"
      :media-list="mediaStore.scopedMedia"
      :initial-index="viewerIndex"
      @close="closeViewer"
    />

    <!-- Slideshow overlay -->
    <SlideshowViewer
      v-if="slideshowOpen"
      :media-list="mediaStore.scopedMedia"
      @close="closeSlideshow"
    />

    <!-- Scan dialog -->
    <ScanDialog
      v-if="scanStore.phase !== 'idle'"
      @close="closeScan"
    />

    <!-- Similarity Settings dialog -->
    <SimilaritySettings
      v-if="simSettingsOpen"
      @close="simSettingsOpen = false"
    />

    <!-- Duplicate Finder dialog -->
    <DuplicateFinder
      v-if="dupFinderOpen"
      @close="dupFinderOpen = false"
    />

    <!-- Upscale dialog -->
    <UpscaleDialog
      v-if="upscaleDialogOpen"
      :media-items="upscaleTargets"
      @close="upscaleDialogOpen = false"
      @submitted="upscaleQueueOpen = true"
    />

    <!-- Upscale Queue -->
    <UpscaleQueue
      v-if="upscaleQueueOpen"
      @close="upscaleQueueOpen = false"
    />

    <!-- Config dialog -->
    <ConfigDialog
      v-if="configOpen"
      @close="configOpen = false"
    />

    <!-- Folder dialogs — overlays that can be opened from anywhere via
         useFoldersUi. -->
    <NewFolderDialog
      v-if="foldersUi.newFolderOpen.value || foldersUi.renameFolderId.value"
    />
    <SmartFolderEditor v-if="foldersUi.smartEditorOpen.value !== null" />
    <FolderKebabMenu />

    <!-- Toast host — single slot, non-blocking -->
    <ToastHost />
  </div>
</template>

<style scoped>
.app-shell {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.center-stack {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

.grid-wrap {
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.loading-overlay {
  position: fixed;
  inset: 0;
  z-index: 800;
  background: rgba(0, 0, 0, 0.3);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  color: #fff;
  font-size: 14px;
}

.loading-spinner {
  width: 32px;
  height: 32px;
  border: 3px solid rgba(255, 255, 255, 0.2);
  border-top-color: #fff;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}
</style>
