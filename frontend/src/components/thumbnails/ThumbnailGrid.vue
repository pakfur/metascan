<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import type { Media } from '../../types/media'
import { useMediaStore } from '../../stores/media'
import { useSettingsStore } from '../../stores/settings'
import { useSimilarityStore } from '../../stores/similarity'
import ThumbnailCard from './ThumbnailCard.vue'
import SimilarityBanner from './SimilarityBanner.vue'

const emit = defineEmits<{
  open: [media: Media]
  upscale: [items: Media[]]
}>()

const mediaStore = useMediaStore()
const settingsStore = useSettingsStore()
const simStore = useSimilarityStore()

const container = ref<HTMLElement | null>(null)
const scrollTop = ref(0)
const containerHeight = ref(600)

// Context menu state
const contextMenu = ref<{ x: number; y: number; media: Media } | null>(null)

const gap = 8
const padding = 12

const cellSize = computed(() => settingsStore.thumbnailSize[0] + gap)

// Use similarity results when active, otherwise normal media
const displayList = computed(() =>
  simStore.active ? simStore.filteredResults : mediaStore.displayedMedia
)

const columns = computed(() => {
  if (!container.value) return 4
  const w = container.value.clientWidth - padding * 2
  return Math.max(1, Math.floor(w / cellSize.value))
})

const rowCount = computed(() =>
  Math.ceil(displayList.value.length / columns.value)
)

const totalHeight = computed(() => rowCount.value * cellSize.value + padding * 2)

const startRow = computed(() =>
  Math.max(0, Math.floor((scrollTop.value - padding) / cellSize.value) - 1)
)

const visibleRows = computed(() =>
  Math.ceil(containerHeight.value / cellSize.value) + 3
)

const endRow = computed(() =>
  Math.min(rowCount.value, startRow.value + visibleRows.value)
)

const visibleItems = computed(() => {
  const items: { media: Media; row: number; col: number }[] = []
  const cols = columns.value
  const displayed = displayList.value
  for (let row = startRow.value; row < endRow.value; row++) {
    for (let col = 0; col < cols; col++) {
      const idx = row * cols + col
      if (idx < displayed.length) {
        items.push({ media: displayed[idx], row, col })
      }
    }
  }
  return items
})

function onScroll() {
  if (container.value) {
    scrollTop.value = container.value.scrollTop
  }
}

function updateSize() {
  if (container.value) {
    containerHeight.value = container.value.clientHeight
  }
}

let resizeObserver: ResizeObserver | null = null

onMounted(() => {
  updateSize()
  if (container.value) {
    resizeObserver = new ResizeObserver(updateSize)
    resizeObserver.observe(container.value)
  }
  document.addEventListener('click', closeContextMenu)
})

onUnmounted(() => {
  resizeObserver?.disconnect()
  document.removeEventListener('click', closeContextMenu)
})

// Reset scroll on list change
watch(() => displayList.value.length, () => {
  nextTick(() => {
    if (container.value) container.value.scrollTop = 0
    scrollTop.value = 0
  })
})

function onSelect(media: Media) {
  mediaStore.selectMedia(media)
}

function onContextMenu(media: Media, e: MouseEvent) {
  mediaStore.selectMedia(media)
  contextMenu.value = { x: e.clientX, y: e.clientY, media }
}

function closeContextMenu() {
  contextMenu.value = null
}

function ctxFindSimilar() {
  if (contextMenu.value) {
    simStore.findSimilar(contextMenu.value.media)
    contextMenu.value = null
  }
}

function ctxUpscale() {
  if (contextMenu.value) {
    emit('upscale', [contextMenu.value.media])
    contextMenu.value = null
  }
}

function ctxDelete() {
  if (contextMenu.value) {
    const media = contextMenu.value.media
    contextMenu.value = null
    if (confirm(`Delete "${media.file_name}"?`)) {
      mediaStore.removeMedia(media)
    }
  }
}
</script>

<template>
  <div class="thumbnail-grid-wrapper">
    <SimilarityBanner v-if="simStore.active" />

    <div ref="container" class="thumbnail-grid" @scroll="onScroll">
      <!-- Empty state -->
      <div v-if="displayList.length === 0 && !mediaStore.loading" class="empty-state">
        <span class="empty-icon">🖼</span>
        <span>No media to display</span>
        <span class="empty-hint">Try adjusting your filters or running a scan</span>
      </div>

      <div v-else class="scroll-spacer" :style="{ height: totalHeight + 'px' }">
        <div
          v-for="item in visibleItems"
          :key="item.media.file_path"
          class="grid-cell"
          :style="{
            position: 'absolute',
            left: padding + item.col * cellSize + 'px',
            top: padding + item.row * cellSize + 'px',
            width: settingsStore.thumbnailSize[0] + 'px',
            height: settingsStore.thumbnailSize[1] + 'px',
          }"
        >
          <ThumbnailCard
            :media="item.media"
            :size="settingsStore.thumbnailSize[0]"
            :selected="mediaStore.selectedMedia?.file_path === item.media.file_path"
            @click="onSelect(item.media)"
            @dblclick="emit('open', item.media)"
            @contextmenu.prevent="onContextMenu(item.media, $event)"
          />
        </div>
      </div>
    </div>

    <!-- Context menu -->
    <Teleport to="body">
      <div
        v-if="contextMenu"
        class="context-menu"
        :style="{ left: contextMenu.x + 'px', top: contextMenu.y + 'px' }"
      >
        <button @click="emit('open', contextMenu!.media); closeContextMenu()">
          Open
        </button>
        <button @click="ctxFindSimilar">Find Similar</button>
        <button @click="ctxUpscale">Upscale</button>
        <hr />
        <button class="danger" @click="ctxDelete">Delete</button>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.thumbnail-grid-wrapper {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.thumbnail-grid {
  overflow-y: auto;
  overflow-x: hidden;
  flex: 1;
  background: var(--surface-ground);
}

.scroll-spacer {
  position: relative;
  width: 100%;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  gap: 8px;
  color: var(--text-color-secondary);
  font-size: 14px;
}

.empty-icon {
  font-size: 48px;
  opacity: 0.4;
}

.empty-hint {
  font-size: 12px;
  opacity: 0.7;
}
</style>

<style>
/* Context menu - unscoped so Teleport works */
.context-menu {
  position: fixed;
  z-index: 2000;
  background: var(--surface-section, #fff);
  border: 1px solid var(--surface-border, #e2e8f0);
  border-radius: 8px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.15);
  padding: 4px;
  min-width: 160px;
}

.context-menu button {
  display: block;
  width: 100%;
  padding: 6px 12px;
  border: none;
  background: transparent;
  color: var(--text-color, #1e293b);
  font-size: 13px;
  text-align: left;
  cursor: pointer;
  border-radius: 4px;
}

.context-menu button:hover {
  background: var(--surface-hover, #f1f5f9);
}

.context-menu button.danger {
  color: var(--danger-color, #ef4444);
}

.context-menu hr {
  border: none;
  border-top: 1px solid var(--surface-border, #e2e8f0);
  margin: 4px 0;
}
</style>
