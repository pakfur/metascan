<script setup lang="ts">
import ToastHost from './components/layout/ToastHost.vue'
import { useWebSocket } from './composables/useWebSocket'
import { useFoldersStore } from './stores/folders'

const foldersStore = useFoldersStore()

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
</script>

<template>
  <router-view />
  <ToastHost />
</template>
