<script setup lang="ts">
import { ref, computed } from 'vue'
import type { Media } from '../../types/media'
import { useFoldersStore } from '../../stores/folders'
import { useMediaStore } from '../../stores/media'
import { useSettingsStore, type ThumbnailSize } from '../../stores/settings'
import { useContentSearch } from '../../composables/useContentSearch'
import ThumbnailGrid from '../thumbnails/ThumbnailGrid.vue'
import MobileFolderMenu from './MobileFolderMenu.vue'

const emit = defineEmits<{
  open: [media: Media]
  slideshow: []
}>()

const foldersStore = useFoldersStore()
const mediaStore = useMediaStore()
const settingsStore = useSettingsStore()

const optionsOpen = ref(false)
const folderMenuOpen = ref(false)
const searchOpen = ref(false)
const { query: searchQuery, submit: submitSearch, clear: clearSearch } = useContentSearch()

const scopeLabel = computed(() => {
  if (foldersStore.isLibraryScope) return 'Library'
  return foldersStore.activeFolder()?.name ?? 'Library'
})

const sizes: { value: ThumbnailSize; label: string }[] = [
  { value: 'small', label: 'S' },
  { value: 'medium', label: 'M' },
  { value: 'large', label: 'L' },
]

const sortOptions = [
  { label: 'Date Added', value: 'date_added' },
  { label: 'Date Modified', value: 'date_modified' },
  { label: 'Name', value: 'file_name' },
]

function onSlideshow() {
  optionsOpen.value = false
  emit('slideshow')
}

function onSearchKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter') submitSearch()
}
function toggleSearch() {
  searchOpen.value = !searchOpen.value
  if (!searchOpen.value) clearSearch()
}
</script>

<template>
  <div class="mobile-shell">
    <header class="m-topbar">
      <button class="m-folder-btn" type="button" @click="folderMenuOpen = true">
        <i class="pi pi-folder" />
        <span class="m-folder-name">{{ scopeLabel }}</span>
        <i class="pi pi-chevron-down" />
      </button>

      <div class="m-topbar-actions">
        <button
          class="m-icon-btn"
          type="button"
          aria-label="Search"
          @click="toggleSearch"
        >
          <i class="pi pi-search" />
        </button>
        <button
          class="m-icon-btn"
          type="button"
          aria-label="Options"
          @click="optionsOpen = !optionsOpen"
        >
          <i class="pi pi-ellipsis-v" />
        </button>
      </div>
    </header>

    <div v-if="searchOpen" class="m-search-row">
      <i class="pi pi-search m-search-icon" />
      <input
        v-model="searchQuery"
        class="m-search-input"
        type="search"
        placeholder="Search by content…"
        @keydown="onSearchKeydown"
      />
      <button
        v-if="searchQuery"
        class="m-icon-btn"
        type="button"
        aria-label="Clear"
        @click="clearSearch"
      >
        <i class="pi pi-times" />
      </button>
      <button class="m-search-go" type="button" @click="submitSearch">Search</button>
    </div>

    <!-- Options sheet: sort, thumbnail size, slideshow -->
    <div v-if="optionsOpen" class="m-sheet-backdrop" @click.self="optionsOpen = false">
      <div class="m-sheet">
        <div class="m-sheet-row">
          <span class="m-sheet-label">Size</span>
          <div class="m-size-presets">
            <button
              v-for="s in sizes"
              :key="s.value"
              type="button"
              :class="['m-size-btn', { active: settingsStore.thumbnailSizeLabel === s.value }]"
              @click="settingsStore.setThumbnailSize(s.value)"
            >
              {{ s.label }}
            </button>
          </div>
        </div>

        <div class="m-sheet-row">
          <span class="m-sheet-label">Sort</span>
          <select
            class="m-select"
            :value="mediaStore.sortOrder"
            @change="mediaStore.setSortOrder(($event.target as HTMLSelectElement).value)"
          >
            <option v-for="opt in sortOptions" :key="opt.value" :value="opt.value">
              {{ opt.label }}
            </option>
          </select>
        </div>

        <button class="m-sheet-action" type="button" @click="onSlideshow">
          <i class="pi pi-play" /> Start slideshow
        </button>
      </div>
    </div>

    <div class="m-grid-wrap">
      <ThumbnailGrid :mobile="true" @open="emit('open', $event)" />
    </div>

    <MobileFolderMenu v-model="folderMenuOpen" />
  </div>
</template>

<style scoped>
.mobile-shell {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

.m-topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 12px;
  padding-top: calc(8px + env(safe-area-inset-top));
  border-bottom: 1px solid var(--surface-border);
  background: var(--surface-section);
  flex-shrink: 0;
}

.m-folder-btn {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border: 1px solid var(--surface-border);
  border-radius: 8px;
  background: var(--surface-card);
  color: var(--text-color);
  font-size: 15px;
  max-width: 70vw;
}

.m-folder-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.m-topbar-actions {
  display: flex;
  align-items: center;
  gap: 4px;
}

.m-icon-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--text-color);
  font-size: 18px;
}

.m-icon-btn:active {
  background: var(--surface-hover);
}

.m-search-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--surface-border);
  background: var(--surface-section);
  flex-shrink: 0;
}

.m-search-icon {
  color: var(--text-color-secondary);
  font-size: 14px;
}

.m-search-input {
  flex: 1;
  min-width: 0;
  padding: 8px 10px;
  border: 1px solid var(--surface-border);
  border-radius: 8px;
  background: var(--surface-card);
  color: var(--text-color);
  font-size: 16px; /* 16px avoids iOS Safari zoom-on-focus */
}

.m-search-go {
  padding: 8px 14px;
  border: none;
  border-radius: 8px;
  background: var(--primary-color);
  color: #fff;
  font-size: 14px;
  font-weight: 600;
}

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
  padding: 16px;
  padding-bottom: calc(16px + env(safe-area-inset-bottom));
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.m-sheet-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.m-sheet-label {
  font-size: 14px;
  color: var(--text-color-secondary);
  font-weight: 600;
}

.m-size-presets {
  display: flex;
  gap: 2px;
  background: var(--surface-ground);
  border-radius: 8px;
  overflow: hidden;
}

.m-size-btn {
  padding: 8px 16px;
  border: none;
  background: transparent;
  color: var(--text-color);
  font-size: 14px;
  font-weight: 600;
}

.m-size-btn.active {
  background: var(--primary-color);
  color: #fff;
}

.m-select {
  padding: 8px 10px;
  border: 1px solid var(--surface-border);
  border-radius: 8px;
  background: var(--surface-card);
  color: var(--text-color);
  font-size: 14px;
}

.m-sheet-action {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 12px;
  border: none;
  border-radius: 10px;
  background: var(--primary-color);
  color: #fff;
  font-size: 15px;
  font-weight: 600;
}

.m-grid-wrap {
  flex: 1;
  min-height: 0;
  overflow: hidden;
}
</style>
