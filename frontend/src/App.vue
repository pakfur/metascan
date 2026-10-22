<script setup lang="ts">
import ToastHost from './components/layout/ToastHost.vue'
import { useWebSocket } from './composables/useWebSocket'
import { useFoldersStore } from './stores/folders'
import { useT2iStore } from './stores/t2i'
import { useCategoriesStore } from './stores/categories'
import { useTasteStore } from './stores/taste'
import type { CategoriesWsEvent } from './types/websocket'
import type { TasteWsEvent } from './types/taste'

const foldersStore = useFoldersStore()
const t2iStore = useT2iStore()
const categoriesStore = useCategoriesStore()
const tasteStore = useTasteStore()

// Scores for the Taste sort (empty until a model exists).
tasteStore.load().catch((e) => console.error('Failed to load taste scores', e))

// Live-sync folder mutations from other tabs / sessions.
useWebSocket('folders', (event, data) => {
  const payload = data as Record<string, unknown>
  if (event === 'folder_created') {
    foldersStore.onFolderCreated(
      payload as unknown as { folder: import('./api/folders').FolderRecord },
    )
  } else if (event === 'folder_updated') {
    foldersStore.onFolderUpdated(
      payload as unknown as { folder: import('./api/folders').FolderRecord },
    )
  } else if (event === 'folder_deleted') {
    foldersStore.onFolderDeleted(payload as unknown as { id: string })
  } else if (event === 'folder_items_changed') {
    foldersStore.onFolderItemsChanged(
      payload as unknown as {
        folder_id: string
        added: string[]
        removed: string[]
      },
    )
  }
})

// Category mutations from any tab (or a sticky-category verdict) refresh the
// list the media viewer's category picker reads.
useWebSocket('categories', (event) => {
  if (event === ('categories_changed' satisfies CategoriesWsEvent)) {
    categoriesStore.load().catch((e) => console.error('Failed to load categories', e))
  }
})

// Taste scorer: new scores refetch the map the Taste sort reads; training
// lifecycle events refresh the Models tab status. Always on so the sort stays
// current whether or not the Models tab is open.
useWebSocket('taste', (event) => {
  if (event === ('scores_updated' satisfies TasteWsEvent)) {
    tasteStore.load().catch((e) => console.error('Failed to load taste scores', e))
    tasteStore.loadStatus().catch((e) => console.error('Failed to load taste status', e))
  } else if (
    event === ('training_started' satisfies TasteWsEvent) ||
    event === ('training_complete' satisfies TasteWsEvent) ||
    event === ('training_failed' satisfies TasteWsEvent)
  ) {
    tasteStore.loadStatus().catch((e) => console.error('Failed to load taste status', e))
  }
})

// New i2v clips change the "Has I2V video" smart-folder rule's membership.
useWebSocket('i2v', (event) => {
  if (event === 'i2v_videos_changed') void foldersStore.refreshI2vSources()
})

// New (or deleted) T2I images change the "Generated with T2I" smart-folder
// rule's membership and belong in the library grid. Always on, so it works
// whether or not the T2I dialog is open: batches keep running on the server
// after the dialog closes. The grid reload is the store's own debounced
// function, which the dialog's forwarder shares, so an open dialog does not
// reload twice.
useWebSocket('t2i', (event) => {
  if (event === 't2i_images_changed') {
    void foldersStore.refreshT2iPaths()
    void t2iStore.scheduleMediaReload()
  }
})
</script>

<template>
  <router-view />
  <ToastHost />
</template>
