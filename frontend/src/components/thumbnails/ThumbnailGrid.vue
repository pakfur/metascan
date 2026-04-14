<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import type { Media } from '../../types/media'
import { useMediaStore } from '../../stores/media'
import { useSettingsStore } from '../../stores/settings'
import ThumbnailCard from './ThumbnailCard.vue'

defineEmits<{
  open: [media: Media]
}>()

const mediaStore = useMediaStore()
const settingsStore = useSettingsStore()

const container = ref<HTMLElement | null>(null)
const scrollTop = ref(0)
const containerHeight = ref(600)

const gap = 8
const padding = 12

const cellSize = computed(() => settingsStore.thumbnailSize[0] + gap)

const columns = computed(() => {
  if (!container.value) return 4
  const w = container.value.clientWidth - padding * 2
  return Math.max(1, Math.floor(w / cellSize.value))
})

const rowCount = computed(() =>
  Math.ceil(mediaStore.displayedMedia.length / columns.value)
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
  const displayed = mediaStore.displayedMedia
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
})

onUnmounted(() => {
  resizeObserver?.disconnect()
})

// Reset scroll on filter change
watch(() => mediaStore.displayedMedia.length, () => {
  nextTick(() => {
    if (container.value) container.value.scrollTop = 0
    scrollTop.value = 0
  })
})

function onSelect(media: Media) {
  mediaStore.selectMedia(media)
}

function onContextMenu(media: Media, _e: MouseEvent) {
  // Context menu handled by ThumbnailCard
  mediaStore.selectMedia(media)
}
</script>

<template>
  <div ref="container" class="thumbnail-grid" @scroll="onScroll">
    <div class="scroll-spacer" :style="{ height: totalHeight + 'px' }">
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
          @dblclick="$emit('open', item.media)"
          @contextmenu.prevent="onContextMenu(item.media, $event)"
        />
      </div>
    </div>
  </div>
</template>

<style scoped>
.thumbnail-grid {
  overflow-y: auto;
  overflow-x: hidden;
  height: 100%;
  background: var(--surface-ground);
}

.scroll-spacer {
  position: relative;
  width: 100%;
}
</style>
