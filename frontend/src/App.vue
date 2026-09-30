<script setup lang="ts">
import ToastHost from './components/layout/ToastHost.vue'
import { useWebSocket } from './composables/useWebSocket'
import { useFoldersStore } from './stores/folders'
import { useT2iStore } from './stores/t2i'

const foldersStore = useFoldersStore()
const t2iStore = useT2iStore()

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
