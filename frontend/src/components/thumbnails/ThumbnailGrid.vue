<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import type { Media } from '../../types/media'
import { useMediaStore } from '../../stores/media'
import { useSettingsStore } from '../../stores/settings'
import { useSimilarityStore } from '../../stores/similarity'
import ThumbnailCard from './ThumbnailCard.vue'
import SimilarityBanner from './SimilarityBanner.vue'
import { fileName } from '../../utils/path'
import { useFoldersStore } from '../../stores/folders'
import { useFoldersUi } from '../../composables/useFoldersUi'
import { useToast } from '../../composables/useToast'
import { useModelsStore } from '../../stores/models'
import { tagOne } from '../../api/vlm'

const emit = defineEmits<{
  open: [media: Media]
  upscale: [items: Media[]]
  playground: [media: Media]
}>()

const mediaStore = useMediaStore()
const settingsStore = useSettingsStore()
const simStore = useSimilarityStore()
const foldersStore = useFoldersStore()
const foldersUi = useFoldersUi()
const toast = useToast()
const modelsStore = useModelsStore()

const container = ref<HTMLElement | null>(null)
const scrollTop = ref(0)
const containerHeight = ref(600)

// Scroll-velocity gate: suppress thumbnail src assignment while actively
// scrolling. When scroll stops for SCROLL_SETTLE_MS, visible cards load.
// Prevents the HTTP/1.1 connection pool from filling with stale requests
// for rows the user has already scrolled past.
const isScrolling = ref(false)
const SCROLL_SETTLE_MS = 150
let scrollSettleId: ReturnType<typeof setTimeout> | null = null

// Context menu state
const contextMenu = ref<{ x: number; y: number; media: Media } | null>(null)

const gap = 8
const padding = 12

const cellSize = computed(() => settingsStore.thumbnailSize[0] + gap)

// Use similarity results when active; otherwise apply the current folder
// scope. Similarity results are already a narrowed slice and shouldn't be
// re-filtered by scope (the user is searching the library, not the folder).
const displayList = computed(() =>
  simStore.active ? simStore.filteredResults : mediaStore.scopedMedia,
)

// Set of file paths that belong to any manual folder — used to show the
// little blue dot on thumbs that are members (when not already inside
// that folder's scope).
const manualMemberPaths = computed(() => {
  const set = new Set<string>()
  for (const f of foldersStore.manualFolders) {
    for (const p of f.items) set.add(p)
  }
  return set
})

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
  isScrolling.value = true
  if (scrollSettleId) clearTimeout(scrollSettleId)
  scrollSettleId = setTimeout(() => {
    isScrolling.value = false
    scrollSettleId = null
  }, SCROLL_SETTLE_MS)
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
  window.addEventListener('keydown', onGridKeyDown)
})

onUnmounted(() => {
  resizeObserver?.disconnect()
  if (scrollSettleId) clearTimeout(scrollSettleId)
  document.removeEventListener('click', closeContextMenu)
  window.removeEventListener('keydown', onGridKeyDown)
})

// Arrow-key navigation over the grid. Suppressed while any overlay/dialog
// is mounted — those own their own keyboard shortcuts.
function onGridKeyDown(e: KeyboardEvent) {
  if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(e.key)) {
    return
  }
  const tag = (e.target as HTMLElement | null)?.tagName
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return
  if (
    document.querySelector(
      '.media-viewer-overlay, .slideshow-overlay, .dialog-overlay',
    )
  ) {
    return
  }
  const list = displayList.value
  if (list.length === 0) return

  e.preventDefault()

  const curPath = mediaStore.selectedMedia?.file_path
  let idx = curPath ? list.findIndex((m) => m.file_path === curPath) : -1

  if (idx < 0) {
    idx = 0
  } else {
    const cols = columns.value
    switch (e.key) {
      case 'ArrowLeft':
        idx = Math.max(0, idx - 1)
        break
      case 'ArrowRight':
        idx = Math.min(list.length - 1, idx + 1)
        break
      case 'ArrowUp':
        idx = Math.max(0, idx - cols)
        break
      case 'ArrowDown':
        idx = Math.min(list.length - 1, idx + cols)
        break
    }
  }

  mediaStore.selectMedia(list[idx])
  scrollIndexIntoView(idx)
}

function scrollIndexIntoView(idx: number) {
  if (!container.value) return
  const row = Math.floor(idx / columns.value)
  const rowTop = padding + row * cellSize.value
  const rowBottom = rowTop + cellSize.value
  const viewTop = container.value.scrollTop
  const viewBottom = viewTop + containerHeight.value
  if (rowTop < viewTop) {
    container.value.scrollTop = rowTop - padding
  } else if (rowBottom > viewBottom) {
    container.value.scrollTop = rowBottom - containerHeight.value + padding
  }
}

// Called from App.vue when the MediaViewer closes — the user may have
// navigated far from the initially-clicked thumbnail, so we center the
// selected row in the viewport instead of just nudging to the edge.
// No-op if the row is already fully visible.
function scrollSelectedIntoView() {
  const path = mediaStore.selectedMedia?.file_path
  if (!path || !container.value) return
  const list = displayList.value
  const idx = list.findIndex((m) => m.file_path === path)
  if (idx < 0) return
  const row = Math.floor(idx / columns.value)
  const rowTop = padding + row * cellSize.value
  const rowBottom = rowTop + cellSize.value
  const viewTop = container.value.scrollTop
  const viewBottom = viewTop + containerHeight.value
  if (rowTop >= viewTop && rowBottom <= viewBottom) return
  const centered = rowTop - (containerHeight.value - cellSize.value) / 2
  const maxTop = Math.max(0, totalHeight.value - containerHeight.value)
  container.value.scrollTop = Math.max(0, Math.min(centered, maxTop))
}

defineExpose({ scrollSelectedIntoView })

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

async function ctxRetagWithVlm() {
  if (!contextMenu.value) return
  const target = contextMenu.value.media
  closeContextMenu()
  try {
    const { tags } = await tagOne(target.file_path)
    toast.show(`Re-tagged with ${tags.length} tag${tags.length !== 1 ? 's' : ''}`, 'success')
  } catch (e) {
    toast.show(`Re-tag failed: ${e instanceof Error ? e.message : String(e)}`, 'warn')
  }
}

function ctxPlayground() {
  if (!contextMenu.value) return
  const target = contextMenu.value.media
  closeContextMenu()
  emit('playground', target)
}

function ctxDelete() {
  if (contextMenu.value) {
    const media = contextMenu.value.media
    contextMenu.value = null
    if (confirm(`Delete "${media.file_name ?? fileName(media.file_path)}"?`)) {
      mediaStore.removeMedia(media)
    }
  }
}

// Paths targeted by the current context-menu action — if the right-clicked
// media is already part of a multi-selection, all of them; otherwise just
// the one under the cursor. We latch this at menu-open time so changing
// selection via a subsequent click doesn't surprise the user mid-submenu.
const contextTargetPaths = computed<string[]>(() => {
  if (!contextMenu.value) return []
  return [contextMenu.value.media.file_path]
})

const contextInsideManualFolder = computed(() =>
  foldersStore.scope.kind === 'manual' ? foldersStore.scope.id : null,
)

function ctxAddToFolder(folderId: string) {
  if (!contextMenu.value) return
  const paths = contextTargetPaths.value
  const f = foldersStore.manualFolders.find((x) => x.id === folderId)
  if (!f) return
  const added = foldersStore.addToManualFolder(folderId, paths)
  closeContextMenu()
  if (added > 0) {
    toast.show(`${added} item${added === 1 ? '' : 's'} added to ${f.name}`)
  } else {
    toast.show(`Already in ${f.name}`, 'warn')
  }
}

function ctxAddToNewFolder() {
  if (!contextMenu.value) return
  const paths = contextTargetPaths.value
  foldersUi.openNewFolder(paths)
  closeContextMenu()
}

function ctxNewSmartFromSelection() {
  if (!contextMenu.value) return
  const m = contextMenu.value.media
  const modelName =
    Array.isArray(m.model) && m.model.length > 0 ? m.model[0] : null
  foldersUi.openSmartEditor(
    'new',
    modelName
      ? { match: 'all', conditions: [{ field: 'model', op: 'is', value: modelName }] }
      : { match: 'all', conditions: [{ field: 'favorite', op: 'is', value: true }] },
  )
  closeContextMenu()
}

function ctxRemoveFromCurrent() {
  if (!contextMenu.value) return
  const folderId = contextInsideManualFolder.value
  if (!folderId) return
  const paths = contextTargetPaths.value
  const removed = foldersStore.removeFromManualFolder(folderId, paths)
  const f = foldersStore.manualFolders.find((x) => x.id === folderId)
  closeContextMenu()
  if (removed > 0 && f) {
    toast.show(`Removed from ${f.name}`)
  }
}

// Drag source: broadcast the selected media paths so folder rows (drop
// targets) can pick them up. If the dragged item is not in the current
// selection, fall back to a single-item drag.
const dragGhostCount = ref<number | null>(null)
const dragGhostPos = ref({ x: 0, y: 0 })

function onThumbDragStart(e: DragEvent, media: Media) {
  if (!e.dataTransfer) return
  const selected = mediaStore.selectedMedia
  const paths =
    selected && selected.file_path === media.file_path
      ? [media.file_path]
      : [media.file_path]
  e.dataTransfer.setData('application/x-metascan-paths', JSON.stringify(paths))
  e.dataTransfer.effectAllowed = 'copy'
  dragGhostCount.value = paths.length

  // Minimal, translucent drag image — the real visual cue is the pill.
  const ghost = document.createElement('div')
  ghost.style.cssText =
    'width:80px;height:80px;background:#3b82f6;border-radius:6px;opacity:0.5;'
  document.body.appendChild(ghost)
  e.dataTransfer.setDragImage(ghost, 40, 40)
  setTimeout(() => ghost.remove(), 0)
}

function onThumbDrag(e: DragEvent) {
  if (e.clientX || e.clientY) {
    dragGhostPos.value = { x: e.clientX, y: e.clientY }
  }
}

function onThumbDragEnd() {
  dragGhostCount.value = null
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
            :defer-load="isScrolling"
            :in-folder="
              manualMemberPaths.has(item.media.file_path) &&
              foldersStore.scope.kind !== 'manual'
            "
            draggable="true"
            @click="onSelect(item.media)"
            @dblclick="emit('open', item.media)"
            @contextmenu.prevent="onContextMenu(item.media, $event)"
            @dragstart="onThumbDragStart($event, item.media)"
            @drag="onThumbDrag"
            @dragend="onThumbDragEnd"
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
        <button
          v-if="modelsStore.isVlmReady"
          @click="ctxRetagWithVlm"
        >
          Re-tag with Qwen3-VL
        </button>
        <button
          v-if="modelsStore.isVlmReady"
          @click="ctxPlayground"
        >
          Prompt Playground…
        </button>
        <hr />
        <div class="context-sub-host">
          <button type="button" class="sub-anchor">
            <span>Add to folder</span>
            <span class="arrow">▶</span>
          </button>
          <div class="context-sub">
            <button
              v-for="f in foldersStore.manualFolders"
              :key="f.id"
              @click="ctxAddToFolder(f.id)"
            >
              <i class="pi pi-folder" /> {{ f.name }}
            </button>
            <div
              v-if="foldersStore.manualFolders.length === 0"
              class="sub-empty"
            >
              No folders yet
            </div>
            <hr />
            <button @click="ctxAddToNewFolder">
              <i class="pi pi-plus" /> New folder…
            </button>
          </div>
        </div>
        <button @click="ctxNewSmartFromSelection">
          New smart folder from selection…
        </button>
        <template v-if="contextInsideManualFolder">
          <hr />
          <button @click="ctxRemoveFromCurrent">Remove from this folder</button>
        </template>
        <hr />
        <button class="danger" @click="ctxDelete">Delete</button>
      </div>
    </Teleport>

    <!-- Drag-count pill: follows the cursor while a drag is active so the
         user sees exactly how many items will land on the drop target. -->
    <Teleport to="body">
      <div
        v-if="dragGhostCount !== null"
        class="drag-count"
        :style="{
          left: dragGhostPos.x + 12 + 'px',
          top: dragGhostPos.y + 12 + 'px',
        }"
      >
        {{ dragGhostCount }} item{{ dragGhostCount === 1 ? '' : 's' }}
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

.context-menu button i.pi {
  margin-right: 6px;
  font-size: 12px;
  color: var(--text-color-secondary, #64748b);
}

.context-sub-host {
  position: relative;
}

.context-sub-host .sub-anchor {
  display: flex;
  width: 100%;
  align-items: center;
  justify-content: space-between;
  padding: 6px 12px;
  border: none;
  background: transparent;
  color: var(--text-color, #1e293b);
  font-size: 13px;
  text-align: left;
  cursor: pointer;
  border-radius: 4px;
}

.context-sub-host .sub-anchor:hover,
.context-sub-host:hover > .sub-anchor {
  background: var(--surface-hover, #f1f5f9);
}

.context-sub-host .arrow {
  color: var(--text-color-secondary, #64748b);
  font-size: 10px;
}

.context-sub {
  display: none;
  position: absolute;
  left: 100%;
  top: -5px;
  min-width: 220px;
  background: var(--surface-section, #fff);
  border: 1px solid var(--surface-border, #e2e8f0);
  border-radius: 8px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.15);
  padding: 4px;
  z-index: 2001;
}

.context-sub-host:hover .context-sub {
  display: block;
}

.context-sub button {
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

.context-sub button:hover {
  background: var(--surface-hover, #f1f5f9);
}

.context-sub hr {
  border: none;
  border-top: 1px solid var(--surface-border, #e2e8f0);
  margin: 4px 0;
}

.context-sub .sub-empty {
  padding: 6px 12px;
  color: var(--text-color-secondary, #64748b);
  font-size: 12px;
}

.drag-count {
  position: fixed;
  z-index: 2000;
  pointer-events: none;
  background: var(--primary-color, #3b82f6);
  color: #fff;
  font-size: 12px;
  font-weight: 700;
  padding: 6px 12px;
  border-radius: 999px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.15);
}
</style>
