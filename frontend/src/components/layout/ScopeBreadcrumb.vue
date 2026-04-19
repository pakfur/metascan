<script setup lang="ts">
import { computed } from 'vue'
import { useFoldersStore } from '../../stores/folders'
import { useMediaStore } from '../../stores/media'
import { useFoldersUi } from '../../composables/useFoldersUi'

const foldersStore = useFoldersStore()
const mediaStore = useMediaStore()
const ui = useFoldersUi()

interface View {
  icon: string
  name: string
  chip: 'manual' | 'smart' | null
  // Item count uses `scopedMedia` which already respects the active scope.
  count: number
  smartId: string | null
}

const view = computed<View>(() => {
  const scope = foldersStore.scope
  const count = mediaStore.scopedMedia.length
  if (scope.kind === 'library') {
    return { icon: 'pi-images', name: 'Library', chip: null, count, smartId: null }
  }
  const f = foldersStore.activeFolder()
  if (!f) {
    return { icon: 'pi-images', name: 'Library', chip: null, count, smartId: null }
  }
  return {
    icon: f.icon,
    name: f.name,
    chip: f.kind,
    count,
    smartId: f.kind === 'smart' ? f.id : null,
  }
})
</script>

<template>
  <div class="breadcrumb">
    <i class="pi" :class="view.icon" />
    <span class="scope-name">{{ view.name }}</span>
    <span
      v-if="view.chip"
      class="scope-type"
      :class="{ smart: view.chip === 'smart' }"
    >
      {{ view.chip === 'manual' ? 'MANUAL' : 'SMART' }}
    </span>
    <button
      v-if="view.smartId"
      class="edit-rules"
      @click="ui.openSmartEditor(view.smartId!)"
    >
      <i class="pi pi-sliders-h" />Edit rules
    </button>
    <span class="scope-count">
      {{ view.count }} item{{ view.count === 1 ? '' : 's' }}
    </span>
  </div>
</template>

<style scoped>
.breadcrumb {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  border-bottom: 1px solid var(--surface-border);
  background: var(--surface-section);
}

.breadcrumb .pi {
  font-size: 14px;
  color: var(--text-color-secondary);
}

.scope-name {
  font-size: 15px;
  font-weight: 700;
  color: var(--text-color);
}

.scope-type {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 999px;
  font-weight: 600;
  background: color-mix(in srgb, var(--primary-color) 18%, transparent);
  color: var(--primary-color);
}

.scope-type.smart {
  background: color-mix(in srgb, #a855f7 18%, transparent);
  color: #a855f7;
}

.scope-count {
  font-size: 12px;
  color: var(--text-color-secondary);
  margin-left: auto;
  font-variant-numeric: tabular-nums;
}

.edit-rules {
  font-size: 12px;
  padding: 4px 10px;
  border-radius: 6px;
  background: var(--surface-card);
  color: var(--text-color);
  border: 1px solid var(--surface-border);
  cursor: pointer;
  font-family: inherit;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.edit-rules:hover {
  background: var(--surface-hover);
}
</style>
