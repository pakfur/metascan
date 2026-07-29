<script setup lang="ts">
import { computed } from 'vue'
import { useFoldersStore } from '../../stores/folders'
import { useMediaStore } from '../../stores/media'
import type { FolderScope } from '../../types/folders'

defineProps<{
  modelValue: boolean
}>()
const emit = defineEmits<{
  'update:modelValue': [boolean]
}>()

const foldersStore = useFoldersStore()
const mediaStore = useMediaStore()

const libraryCount = computed(() =>
  foldersStore.scopeCount('library', '', mediaStore.displayedMedia),
)

function isActive(scope: FolderScope): boolean {
  const s = foldersStore.scope
  if (scope.kind !== s.kind) return false
  if (scope.kind === 'library') return true
  return 'id' in s && s.id === scope.id
}

function pick(scope: FolderScope) {
  foldersStore.setScope(scope)
  emit('update:modelValue', false)
}
</script>

<template>
  <div v-if="modelValue" class="m-sheet-backdrop" @click.self="emit('update:modelValue', false)">
    <div class="m-sheet m-folder-sheet">
      <div class="m-sheet-handle" />
      <div class="m-folder-list">
        <button
          type="button"
          class="m-folder-item"
          :class="{ active: isActive({ kind: 'library' }) }"
          @click="pick({ kind: 'library' })"
        >
          <i class="pi pi-images" />
          <span class="m-folder-item-name">Library</span>
          <span class="m-folder-count">{{ libraryCount }}</span>
        </button>

        <template v-if="foldersStore.manualFolders.length">
          <div class="m-folder-group">Folders</div>
          <button
            v-for="f in foldersStore.manualFolders"
            :key="f.id"
            type="button"
            class="m-folder-item"
            :class="{ active: isActive({ kind: 'manual', id: f.id }) }"
            @click="pick({ kind: 'manual', id: f.id })"
          >
            <i class="pi" :class="f.icon || 'pi-folder'" />
            <span class="m-folder-item-name">{{ f.name }}</span>
            <span class="m-folder-count">
              {{ foldersStore.scopeCount('manual', f.id, mediaStore.displayedMedia) }}
            </span>
          </button>
        </template>

        <template v-if="foldersStore.smartFolders.length">
          <div class="m-folder-group">Smart Folders</div>
          <button
            v-for="f in foldersStore.smartFolders"
            :key="f.id"
            type="button"
            class="m-folder-item"
            :class="{ active: isActive({ kind: 'smart', id: f.id }) }"
            @click="pick({ kind: 'smart', id: f.id })"
          >
            <i class="pi" :class="f.icon || 'pi-bolt'" />
            <span class="m-folder-item-name">{{ f.name }}</span>
            <span class="m-folder-count">
              {{ foldersStore.scopeCount('smart', f.id, mediaStore.displayedMedia) }}
            </span>
          </button>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
.m-sheet-backdrop {
  position: fixed;
  inset: 0;
  z-index: 1200;
  background: rgba(0, 0, 0, 0.4);
  display: flex;
  align-items: flex-end;
}

.m-sheet {
  width: 100%;
  background: var(--surface-section);
  border-radius: 16px 16px 0 0;
  padding: 8px 8px calc(8px + env(safe-area-inset-bottom));
}

.m-folder-sheet {
  max-height: 70vh;
  display: flex;
  flex-direction: column;
}

.m-sheet-handle {
  width: 40px;
  height: 4px;
  border-radius: 2px;
  background: var(--surface-border);
  margin: 4px auto 8px;
  flex-shrink: 0;
}

.m-folder-list {
  overflow-y: auto;
}

.m-folder-group {
  padding: 12px 12px 4px;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  color: var(--text-color-secondary);
}

.m-folder-item {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  padding: 14px 12px;
  border: none;
  background: transparent;
  color: var(--text-color);
  font-size: 16px;
  text-align: left;
  border-radius: 10px;
}

.m-folder-item.active {
  background: color-mix(in srgb, var(--primary-color) 15%, transparent);
  color: var(--primary-color);
}

.m-folder-item-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.m-folder-count {
  font-size: 13px;
  color: var(--text-color-secondary);
  font-variant-numeric: tabular-nums;
}

.m-folder-item.active .m-folder-count {
  color: var(--primary-color);
}
</style>
