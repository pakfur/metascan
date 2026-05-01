<script setup lang="ts">
import { computed } from 'vue'
import type { MenuItem } from 'primevue/menuitem'
import { useFilterStore, type ViewPreset } from '../../stores/filters'
import { useMediaStore } from '../../stores/media'
import { useSettingsStore, type ThumbnailSize } from '../../stores/settings'

const emit = defineEmits<{
  slideshow: []
}>()

const filterStore = useFilterStore()
const mediaStore = useMediaStore()
const settingsStore = useSettingsStore()

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

function isActive(view: ViewPreset) {
  return filterStore.activeView === view
}

const menuItems = computed<MenuItem[]>(() => {
  const mk = (label: string, view: ViewPreset, icon: string): MenuItem => ({
    label,
    icon,
    class: isActive(view) ? 'view-item-active' : undefined,
    command: () => filterStore.setView(view),
  })
  return [
    mk('Home', 'home', 'pi pi-home'),
    mk('Video', 'video', 'pi pi-video'),
    mk('Images', 'images', 'pi pi-image'),
    mk('Favorites', 'favorites', 'pi pi-heart'),
  ]
})
</script>

<template>
  <Menubar :model="menuItems" class="view-menubar">
    <template #end>
      <div class="menubar-end">
        <div class="size-presets">
          <button
            v-for="s in sizes"
            :key="s.value"
            :class="['size-btn', { active: settingsStore.thumbnailSizeLabel === s.value }]"
            @click="settingsStore.setThumbnailSize(s.value)"
          >
            {{ s.label }}
          </button>
        </div>

        <div class="right-group">
          <select
            class="sort-select"
            :value="mediaStore.sortOrder"
            @change="mediaStore.setSortOrder(($event.target as HTMLSelectElement).value)"
          >
            <option v-for="opt in sortOptions" :key="opt.value" :value="opt.value">
              {{ opt.label }}
            </option>
          </select>

          <Button
            label="Slideshow"
            icon="pi pi-play"
            severity="secondary"
            size="small"
            @click="emit('slideshow')"
          />

          <span class="media-count">{{ mediaStore.displayedMedia.length }} items</span>
        </div>
      </div>
    </template>
  </Menubar>
</template>

<style scoped>
.view-menubar {
  border-radius: 0;
  border-left: none;
  border-right: none;
  border-top: none;
}

.menubar-end {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding-left: 24px;
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

.right-group {
  display: flex;
  align-items: center;
  gap: 12px;
}

.sort-select {
  padding: 4px 8px;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-card);
  color: var(--text-color);
  font-size: 13px;
}

.media-count {
  font-size: 13px;
  color: var(--text-color-secondary);
  white-space: nowrap;
}
</style>

<style>
.view-menubar .view-item-active > .p-menubar-item-content {
  background: var(--primary-color);
  color: #fff;
}

.view-menubar .view-item-active > .p-menubar-item-content .p-menubar-item-icon,
.view-menubar .view-item-active > .p-menubar-item-content .p-menubar-item-label {
  color: #fff;
}
</style>
