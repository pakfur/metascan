<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { fetchAllMedia } from '../../api/media'
import { thumbnailUrl } from '../../api/client'
import { useFoldersStore, matches } from '../../stores/folders'
import type { Media } from '../../types/media'

const emit = defineEmits<{ select: [path: string]; close: [] }>()

const foldersStore = useFoldersStore()

const loading = ref(true)
const error = ref<string | null>(null)
const images = ref<Media[]>([])
const query = ref('')
const folderId = ref('')
const sortOrder = ref('date_added')

const SORT_OPTIONS = [
  { label: 'Date Added', value: 'date_added' },
  { label: 'Date Modified', value: 'date_modified' },
  { label: 'Name', value: 'file_name' },
]

async function loadMedia() {
  loading.value = true
  error.value = null
  try {
    images.value = (await fetchAllMedia(sortOrder.value)).filter((m) => !m.is_video)
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  loadMedia()
  // StoryboardView doesn't load the folders store the way LibraryView does —
  // fetch it here so the dropdown has data. Refetching when already loaded
  // is harmless (server truth replaces in-memory state).
  if (
    foldersStore.manualFolders.length === 0 &&
    foldersStore.smartFolders.length === 0
  ) {
    foldersStore.loadFolders()
  }
})

watch(sortOrder, () => loadMedia())

const folderOptions = computed(() => [
  { id: '', name: 'All folders' },
  ...foldersStore.manualFolders.map((f) => ({ id: f.id, name: f.name })),
  ...foldersStore.smartFolders.map((f) => ({ id: f.id, name: f.name })),
])

const filtered = computed(() => {
  // Reading tagPathsVersion makes tag-based smart-folder membership reactive
  // to the store's async tag-path fetch (same pattern as scopeMedia).
  void foldersStore.tagPathsVersion
  let items = images.value
  const fid = folderId.value
  if (fid) {
    const manual = foldersStore.manualFolders.find((f) => f.id === fid)
    if (manual) {
      const set = new Set(manual.items)
      items = items.filter((m) => set.has(m.file_path))
    } else {
      const smart = foldersStore.smartFolders.find((f) => f.id === fid)
      items = smart ? items.filter((m) => matches(m, smart.rules)) : []
    }
  }
  const q = query.value.trim().toLowerCase()
  if (q) {
    items = items.filter((m) => m.file_path.toLowerCase().includes(q))
  }
  return items
})

function basename(path: string): string {
  return path.split(/[\\/]/).pop() ?? path
}

// ---- virtual scrolling ----------------------------------------------------
// Same windowing technique as ThumbnailGrid: a spacer div sized to the full
// list, with only the rows near the viewport rendered as absolutely
// positioned tiles.
const container = ref<HTMLElement | null>(null)
const scrollTop = ref(0)
const containerHeight = ref(400)
const containerWidth = ref(680)

const TILE_W = 110
const TILE_H = 132
const GAP = 10

const cellW = TILE_W + GAP
const cellH = TILE_H + GAP

const columns = computed(() =>
  Math.max(1, Math.floor((containerWidth.value + GAP) / cellW)),
)

const rowCount = computed(() => Math.ceil(filtered.value.length / columns.value))

const totalHeight = computed(() => Math.max(0, rowCount.value * cellH - GAP))

const startRow = computed(() =>
  Math.max(0, Math.floor(scrollTop.value / cellH) - 1),
)

const endRow = computed(() =>
  Math.min(rowCount.value, startRow.value + Math.ceil(containerHeight.value / cellH) + 3),
)

const visibleItems = computed(() => {
  const items: { media: Media; row: number; col: number }[] = []
  const cols = columns.value
  const list = filtered.value
  for (let row = startRow.value; row < endRow.value; row++) {
    for (let col = 0; col < cols; col++) {
      const idx = row * cols + col
      if (idx < list.length) items.push({ media: list[idx], row, col })
    }
  }
  return items
})

function onScroll() {
  if (container.value) scrollTop.value = container.value.scrollTop
}

let resizeObserver: ResizeObserver | null = null

function updateSize() {
  if (!container.value) return
  containerHeight.value = container.value.clientHeight
  containerWidth.value = container.value.clientWidth
}

onMounted(() => {
  nextTick(() => {
    updateSize()
    if (container.value) {
      resizeObserver = new ResizeObserver(updateSize)
      resizeObserver.observe(container.value)
    }
  })
})

onUnmounted(() => {
  resizeObserver?.disconnect()
})

// Back to the top whenever the visible list changes shape.
watch([folderId, query, sortOrder], () => {
  nextTick(() => {
    if (container.value) container.value.scrollTop = 0
    scrollTop.value = 0
  })
})
</script>

<template>
  <div class="picker-overlay" @click.self="emit('close')">
    <div class="picker-card">
      <div class="picker-header">
        <h4>Choose a reference image</h4>
        <input
          v-model="query"
          type="text"
          class="picker-search"
          placeholder="Filter by file name…"
        />
        <button type="button" class="close-btn" title="Close" @click="emit('close')">
          &times;
        </button>
      </div>

      <div class="picker-controls">
        <label class="control">
          <span class="control-label">Folder</span>
          <select v-model="folderId" class="picker-select">
            <option v-for="f in folderOptions" :key="f.id" :value="f.id">
              {{ f.name }}
            </option>
          </select>
        </label>
        <label class="control">
          <span class="control-label">Sort</span>
          <select v-model="sortOrder" class="picker-select">
            <option v-for="opt in SORT_OPTIONS" :key="opt.value" :value="opt.value">
              {{ opt.label }}
            </option>
          </select>
        </label>
        <span class="picker-count" v-if="!loading && !error">
          {{ filtered.length }} image{{ filtered.length === 1 ? '' : 's' }}
        </span>
      </div>

      <p v-if="error" class="picker-msg error">{{ error }}</p>
      <p v-else-if="loading" class="picker-msg">Loading library…</p>
      <p v-else-if="filtered.length === 0" class="picker-msg">No matching images.</p>

      <div v-else ref="container" class="picker-grid" @scroll="onScroll">
        <div class="scroll-spacer" :style="{ height: totalHeight + 'px' }">
          <button
            v-for="item in visibleItems"
            :key="item.media.file_path"
            type="button"
            class="picker-tile"
            :title="item.media.file_path"
            :style="{
              left: item.col * cellW + 'px',
              top: item.row * cellH + 'px',
              width: TILE_W + 'px',
              height: TILE_H + 'px',
            }"
            @click="emit('select', item.media.file_path)"
          >
            <img :src="thumbnailUrl(item.media.file_path)" alt="" loading="lazy" />
            <span class="tile-name">{{ basename(item.media.file_path) }}</span>
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.picker-overlay {
  position: fixed;
  inset: 0;
  z-index: 950;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
}

.picker-card {
  background: var(--surface-section);
  border-radius: 12px;
  padding: 16px 20px 18px;
  width: 860px;
  max-width: 94vw;
  height: 84vh;
  display: flex;
  flex-direction: column;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
}

.picker-header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 10px;
}

h4 {
  margin: 0;
  font-size: 14px;
  color: var(--text-color);
  flex-shrink: 0;
}

.picker-search {
  flex: 1;
  min-width: 0;
  padding: 6px 10px;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-card);
  color: var(--text-color);
  font-size: 13px;
  font-family: inherit;
}

.picker-search:focus {
  outline: none;
  border-color: var(--primary-color);
}

.picker-controls {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 12px;
}

.control {
  display: flex;
  align-items: center;
  gap: 6px;
}

.control-label {
  font-size: 12px;
  color: var(--text-color-secondary);
}

.picker-select {
  padding: 5px 8px;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-card);
  color: var(--text-color);
  font-size: 13px;
  font-family: inherit;
  max-width: 220px;
}

.picker-select:focus {
  outline: none;
  border-color: var(--primary-color);
}

.picker-count {
  margin-left: auto;
  font-size: 12px;
  color: var(--text-color-secondary);
}

.close-btn {
  background: none;
  border: none;
  color: var(--text-color-secondary);
  font-size: 20px;
  cursor: pointer;
  padding: 0 4px;
  line-height: 1;
  flex-shrink: 0;
}

.close-btn:hover {
  color: var(--text-color);
}

.picker-grid {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  min-height: 120px;
}

.scroll-spacer {
  position: relative;
  width: 100%;
}

.picker-tile {
  position: absolute;
  background: var(--surface-card);
  border: 1px solid var(--surface-border);
  border-radius: 8px;
  padding: 6px;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 4px;
  text-align: left;
}

.picker-tile:hover {
  border-color: var(--primary-color);
}

.picker-tile img {
  width: 100%;
  flex: 1;
  min-height: 0;
  object-fit: cover;
  border-radius: 4px;
  display: block;
}

.tile-name {
  font-size: 11px;
  color: var(--text-color-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex-shrink: 0;
}

.picker-msg {
  padding: 16px 0;
  text-align: center;
  color: var(--text-color-secondary);
  font-size: 13px;
  margin: 0;
}

.picker-msg.error {
  color: var(--danger-color, #e53e3e);
}
</style>
