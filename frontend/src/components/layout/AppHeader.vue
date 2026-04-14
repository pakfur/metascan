<script setup lang="ts">
import { useMediaStore } from '../../stores/media'
import { useSettingsStore } from '../../stores/settings'
import type { ThumbnailSize } from '../../stores/settings'

const mediaStore = useMediaStore()
const settingsStore = useSettingsStore()

const sizes: ThumbnailSize[] = ['small', 'medium', 'large']
const sortOptions = [
  { label: 'Date Added', value: 'date_added' },
  { label: 'File Name', value: 'file_name' },
  { label: 'Date Modified', value: 'date_modified' },
]

function refresh() {
  mediaStore.loadAllMedia()
}
</script>

<template>
  <header class="app-header">
    <div class="header-left">
      <span class="app-title">MetaScan</span>
    </div>

    <div class="header-center">
      <div class="size-presets">
        <button
          v-for="s in sizes"
          :key="s"
          :class="['size-btn', { active: settingsStore.thumbnailSizeLabel === s }]"
          @click="settingsStore.setThumbnailSize(s)"
        >
          {{ s[0].toUpperCase() }}
        </button>
      </div>

      <select
        class="sort-select"
        :value="mediaStore.sortOrder"
        @change="mediaStore.setSortOrder(($event.target as HTMLSelectElement).value)"
      >
        <option v-for="opt in sortOptions" :key="opt.value" :value="opt.value">
          {{ opt.label }}
        </option>
      </select>

      <button class="header-btn" @click="refresh" title="Refresh (F5)">
        Refresh
      </button>

      <span class="media-count">{{ mediaStore.displayedMedia.length }} items</span>
    </div>

    <div class="header-right">
      <label class="fav-toggle">
        <input
          type="checkbox"
          :checked="mediaStore.favoritesOnly"
          @change="mediaStore.favoritesOnly = !mediaStore.favoritesOnly"
        />
        Favorites
      </label>
    </div>
  </header>
</template>

<style scoped>
.app-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 16px;
  background: var(--surface-section);
  border-bottom: 1px solid var(--surface-border);
  min-height: 48px;
  gap: 16px;
}

.app-title {
  font-weight: 700;
  font-size: 18px;
  color: var(--primary-color);
}

.header-center {
  display: flex;
  align-items: center;
  gap: 12px;
}

.size-presets {
  display: flex;
  gap: 2px;
  background: var(--surface-ground);
  border-radius: 6px;
  overflow: hidden;
}

.size-btn {
  padding: 4px 12px;
  border: none;
  background: transparent;
  color: var(--text-color);
  cursor: pointer;
  font-size: 13px;
  font-weight: 600;
}

.size-btn.active {
  background: var(--primary-color);
  color: #fff;
}

.sort-select {
  padding: 4px 8px;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-card);
  color: var(--text-color);
  font-size: 13px;
}

.header-btn {
  padding: 4px 12px;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-card);
  color: var(--text-color);
  cursor: pointer;
  font-size: 13px;
}

.header-btn:hover {
  background: var(--surface-hover);
}

.media-count {
  font-size: 13px;
  color: var(--text-color-secondary);
  white-space: nowrap;
}

.fav-toggle {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 13px;
  color: var(--text-color);
  cursor: pointer;
}

.header-right {
  display: flex;
  align-items: center;
}
</style>
