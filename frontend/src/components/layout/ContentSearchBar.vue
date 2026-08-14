<script setup lang="ts">
import { onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useModelsStore } from '../../stores/models'

const emit = defineEmits<{
  scan: []
  refresh: []
  'upscale-queue': []
  'find-duplicates': []
  'similarity-settings': []
  config: []
}>()

const modelsStore = useModelsStore()
const router = useRouter()

onMounted(() => {
  // Bootstrap the models store so the status chip reflects reality on first
  // render rather than waiting for a WS event.
  void modelsStore.loadStatus().catch(() => {
    // non-fatal — the chip will stay grey and WS events will fill in later
  })
})

function openStoryboards() {
  void router.push({ name: 'storyboard' })
}
</script>

<template>
  <div class="content-search-bar">
    <div class="action-group">
      <Button
        v-tooltip.bottom="'Import'"
        icon="pi pi-file-import" 
        severity="secondary"
        text
        rounded
        aria-label="Import"
        @click="emit('scan')"
      />
      <Button
        v-tooltip.bottom="'Refresh (F5)'"
        icon="pi pi-refresh"
        severity="secondary"
        text
        rounded
        aria-label="Refresh"
        @click="emit('refresh')"
      />
      <Button
        v-tooltip.bottom="'Upscale Queue'"
        icon="pi pi-file-arrow-up"
        severity="secondary"
        text
        rounded
        aria-label="Upscale Queue"
        @click="emit('upscale-queue')"
      />
      <Button
        v-tooltip.bottom="'Find Duplicates (Ctrl+Shift+D)'"
        icon="pi pi-copy"
        severity="secondary"
        text
        rounded
        aria-label="Duplicates"
        @click="emit('find-duplicates')"
      />
      <Button
        v-tooltip.bottom="'Similarity Settings'"
        icon="pi pi-ellipsis-v"
        severity="secondary"
        text
        rounded
        aria-label="Similarity"
        @click="emit('similarity-settings')"
      />
      <Button
        v-tooltip.bottom="'Configuration'"
        icon="pi pi-cog"
        severity="secondary"
        text
        rounded
        aria-label="Config"
        @click="emit('config')"
      />
      <Button
        v-tooltip.bottom="'Storyboards'"
        icon="pi pi-images"
        severity="secondary"
        text
        rounded
        aria-label="Storyboards"
        @click="openStoryboards"
      />
    </div>
  </div>
</template>

<style scoped>
.content-search-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--surface-border);
  background: var(--surface-section);
}

.scan-btn {
  flex-shrink: 0;
}

.action-group {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-left: auto;
  flex-shrink: 0;
}

</style>
